"""实时测量会话与 1 Hz 指标落库。

每个 WebSocket 连接开始时创建（或按 session_id 复用）一条 Measurement；
每跨过一个整秒写入一条 MetricPoint，停止或断线时收尾并记录时长。
写入失败只记录日志，不影响实时指标回传。
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db.base import get_session_factory, is_locked_error
from app.db.models import Measurement, MeasurementSegment, MetricPoint
from app.schemas.analysis import CaptureInfo

logger = logging.getLogger(__name__)

LOCK_RETRIES = 3

METRIC_FIELDS = ("laeq", "lceq", "lzeq", "laf", "lcf", "lafmax", "lcfmax", "lcpeak", "c_minus_a")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _level(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


class MeasurementRecorder:
    """负责一条测量记录的创建、逐秒指标写入与收尾。"""

    def __init__(
        self,
        *,
        session_id: str,
        device_label: str,
        sample_rate: int,
        offset_db: float,
        full_scale_pa: float,
        capture: CaptureInfo | None,
    ) -> None:
        self.session_id = session_id
        self.device_label = device_label
        self.sample_rate = sample_rate
        self.offset_db = offset_db
        self.full_scale_pa = full_scale_pa
        self._capture = capture.model_dump() if capture else {}
        self._last_second = -1
        self._closed = False
        self._base_duration = 0.0
        self._second_offset = 0
        self.points_written = 0
        self.measurement_id = self._open()

    # ------------------------------------------------------------------ 生命周期

    def _open(self) -> str:
        session = get_session_factory()()
        try:
            existing = None
            if self.session_id:
                existing = session.scalar(
                    select(Measurement)
                    .where(Measurement.session_id == self.session_id)
                    .order_by(Measurement.started_at.desc())
                    .limit(1)
                )
            if existing is not None:
                existing.status = "running"
                existing.ended_at = None
                existing.sample_rate = self.sample_rate
                existing.offset_db = self.offset_db
                existing.full_scale_pa = self.full_scale_pa
                if self.device_label:
                    existing.device_label = self.device_label
                existing.capture = self._capture or existing.capture
                self._base_duration = float(existing.duration_s or 0.0)
                self._second_offset = math.ceil(self._base_duration)
                self.points_written = int(existing.point_count or 0)
                session.commit()
                return existing.id

            measurement = Measurement(
                session_id=self.session_id,
                device_label=self.device_label,
                sample_rate=self.sample_rate,
                offset_db=self.offset_db,
                full_scale_pa=self.full_scale_pa,
                status="running",
                capture=self._capture,
            )
            session.add(measurement)
            session.commit()
            session.refresh(measurement)
            return measurement.id
        finally:
            session.close()

    def record_frame(self, frame: dict) -> bool:
        """写入分析器本帧产出的完整整秒指标；同一秒只保留一条。"""
        if self._closed:
            return False

        pending: list[tuple[int, int, dict]] = []
        for item in frame.get("seconds") or []:
            try:
                local_second = int(item.get("t_offset_s"))
            except (TypeError, ValueError):
                continue
            second = self._second_offset + local_second
            if second <= self._last_second:
                continue
            self._last_second = second
            pending.append((second, local_second, item))

        if not pending:
            return False

        for attempt in range(LOCK_RETRIES):
            try:
                self._write_points(pending)
            except Exception as exc:
                if attempt + 1 < LOCK_RETRIES and is_locked_error(exc):
                    time.sleep(0.05 * (attempt + 1))
                    continue
                logger.exception(
                    "failed to persist metric points for measurement %s", self.measurement_id
                )
                return False
            self.points_written += len(pending)
            return True
        return False

    def _write_points(self, pending: list[tuple[int, int, dict]]) -> None:
        session = get_session_factory()()
        try:
            for second, _local, item in pending:
                values = {field: _level(item.get(field)) for field in METRIC_FIELDS}
                session.add(
                    MetricPoint(measurement_id=self.measurement_id, t_offset_s=second, **values)
                )
            measurement = session.get(Measurement, self.measurement_id)
            if measurement is not None:
                last_local = max(local for _second, local, _item in pending)
                measurement.point_count = int(measurement.point_count or 0) + len(pending)
                measurement.duration_s = max(
                    float(measurement.duration_s or 0.0),
                    self._base_duration + last_local + 1,
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_segment(
        self,
        *,
        segment_index: int,
        run_id: str,
        audio_file_id: str,
        duration_s: float,
        interrupted: bool,
    ) -> None:
        session = get_session_factory()()
        try:
            existing = session.scalar(
                select(MeasurementSegment).where(
                    MeasurementSegment.measurement_id == self.measurement_id,
                    MeasurementSegment.segment_index == segment_index,
                )
            )
            if existing is None:
                existing = MeasurementSegment(
                    measurement_id=self.measurement_id, segment_index=segment_index
                )
                session.add(existing)
            existing.run_id = run_id
            existing.audio_file_id = audio_file_id
            existing.duration_s = duration_s
            existing.interrupted = interrupted
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("failed to persist segment for measurement %s", self.measurement_id)
        finally:
            session.close()

    def finish(self, status: str, duration_s: float) -> None:
        """收尾；没有任何指标点且没有录音分段时删除空记录。"""
        if self._closed:
            return
        self._closed = True

        total_duration = round(self._base_duration + max(0.0, duration_s), 3)
        session = get_session_factory()()
        try:
            measurement = session.get(Measurement, self.measurement_id)
            if measurement is None:
                return
            has_segments = (
                session.scalar(
                    select(MeasurementSegment.id)
                    .where(MeasurementSegment.measurement_id == self.measurement_id)
                    .limit(1)
                )
                is not None
            )
            if int(measurement.point_count or 0) <= 0 and not has_segments:
                session.delete(measurement)
                session.commit()
                return

            measurement.status = status
            measurement.ended_at = _now()
            measurement.duration_s = max(self._base_duration, total_duration)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("failed to finish measurement %s", self.measurement_id)
        finally:
            session.close()


def close_stale_measurements() -> int:
    """进程重启后把残留的 running 记录标记为中断，避免工作台显示假在线。"""
    session = get_session_factory()()
    try:
        result = session.execute(
            update(Measurement)
            .where(Measurement.status == "running")
            .values(status="interrupted", ended_at=_now())
        )
        session.commit()
        return int(result.rowcount or 0)
    except Exception:
        session.rollback()
        logger.exception("failed to close stale measurements")
        return 0
    finally:
        session.close()
