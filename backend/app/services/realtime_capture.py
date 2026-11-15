"""实时采集落盘：边测量边写 WAV，停止时创建音频记录并提交一次离线分析。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import numpy as np

from app.core.config import get_settings
from app.db.base import get_session_factory
from app.db.models import AnalysisRun, AudioFile
from app.schemas.analysis import AnalysisConfig, CalibrationConfig, CaptureInfo
from app.services import audit
from app.services.pipeline import ALGORITHM_VERSION

HASH_CHUNK = 1024 * 1024


class RealtimeRecorder:
    """把实时采样写成 16 bit WAV；超过单文件上限后停止写入，但实时分析继续。"""

    def __init__(self, path: Path, sample_rate: int, max_bytes: int) -> None:
        import soundfile as sf

        self.path = path
        self.sample_rate = int(sample_rate)
        self.max_bytes = int(max_bytes)
        self.truncated = False
        self.persisted = False
        self.frames = 0
        self._bytes = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = sf.SoundFile(
            str(path),
            mode="w",
            samplerate=self.sample_rate,
            channels=1,
            subtype="PCM_16",
            format="WAV",
        )

    def write(self, samples: np.ndarray) -> None:
        if self.truncated:
            return
        data = np.asarray(samples, dtype=np.float32).ravel()
        if data.size == 0:
            return
        if self._bytes + data.size * 2 > self.max_bytes:
            self.truncated = True
            return
        self._handle.write(data)
        self._bytes += data.size * 2
        self.frames += data.size

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def discard(self) -> None:
        self.close()
        self.path.unlink(missing_ok=True)


def persist_recording(
    recorder: RealtimeRecorder,
    *,
    offset_db: float,
    full_scale_pa: float,
    capture: CaptureInfo | None = None,
) -> tuple[str, str]:
    """归档录音并创建排队中的离线分析任务，返回 (audio_file_id, run_id)。"""
    recorder.close()
    settings = get_settings()
    filename = f"realtime-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
    relative_path = recorder.path.resolve().relative_to(settings.resolved_data_dir).as_posix()

    calibration = CalibrationConfig(
        mode="calibrated" if offset_db != 0.0 or full_scale_pa != 1.0 else "uncalibrated",
        offset_db=max(-60.0, min(60.0, offset_db)),
        full_scale_pa=full_scale_pa,
    )
    config = AnalysisConfig(calibration=calibration, capture=capture)

    session = get_session_factory()()
    try:
        audio = AudioFile(
            original_filename=filename,
            stored_path=relative_path,
            sha256=_sha256(recorder.path),
            size_bytes=recorder.path.stat().st_size,
            sample_rate=recorder.sample_rate,
            channels=1,
            duration_s=round(recorder.duration_s, 3),
        )
        session.add(audio)
        session.commit()
        session.refresh(audio)

        run = AnalysisRun(
            audio_file_id=audio.id,
            status="queued",
            algorithm_version=ALGORITHM_VERSION,
            config=config.model_dump(),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        recorder.persisted = True
        audit.record(
            "realtime.save",
            entity_type="analysis",
            entity_id=run.id,
            detail={
                "audio_file_id": audio.id,
                "duration_s": audio.duration_s,
                "truncated": recorder.truncated,
                "capture": capture.model_dump() if capture else None,
            },
        )
        return audio.id, run.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
