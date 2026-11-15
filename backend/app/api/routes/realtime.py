"""实时采集 WebSocket：浏览器推送 float32 单声道采样，服务端逐帧回传指标。

录音默认不落盘，客户端显式传 save_audio=True 才会边采边写 WAV；停止时归档并提交
一次离线分析（复用原离线链路）。连接异常断开时，已授权的录音会按分段自动归档，
便于前端重连后继续测量。实时帧本身不落库。

落库与文件 IO 都通过 asyncio.to_thread 卸载到线程池，避免阻塞事件循环影响实时回传。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

import anyio
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import security
from app.core.config import get_settings
from app.schemas.analysis import CaptureInfo
from app.services.measurement import MeasurementRecorder
from app.services.realtime import RealtimeAnalyzer
from app.services.realtime_capture import RealtimeRecorder, persist_recording
from app.workers.runner import submit_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/realtime", tags=["realtime"])

MIN_SAMPLE_RATE = 8000
MAX_SAMPLE_RATE = 192000
MIN_SAVE_SECONDS = 1.0
SEGMENT_HISTORY_LIMIT = 50

_HELLO_HINT = '首条消息必须是 {"type": "hello", "sample_rate": <int>}'

_SESSION_SEGMENTS: dict[str, list[dict]] = {}


@router.websocket("/ws")
async def realtime_stream(websocket: WebSocket) -> None:
    await websocket.accept()

    settings = get_settings()
    if not security.token_matches(websocket.query_params.get("token", ""), settings.access_token):
        await _reject(websocket, "未授权：访问口令缺失或不正确，请在「隐私与校准」页填写")
        return

    try:
        hello = await websocket.receive_json()
    except (WebSocketDisconnect, ValueError):
        await _safe_close(websocket)
        return

    if not isinstance(hello, dict) or hello.get("type") != "hello":
        await _reject(websocket, _HELLO_HINT)
        return

    try:
        sample_rate = int(hello.get("sample_rate") or 0)
    except (TypeError, ValueError):
        sample_rate = 0
    if not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        await _reject(websocket, f"sample_rate 需在 {MIN_SAMPLE_RATE}–{MAX_SAMPLE_RATE} Hz 之间")
        return

    offset_db = _as_float(hello.get("offset_db"), 0.0)
    full_scale_pa = _as_float(hello.get("full_scale_pa"), 1.0)
    if full_scale_pa <= 0:
        full_scale_pa = 1.0
    save_audio = _as_bool(hello.get("save_audio"), False)
    session_id = _as_text(hello.get("session_id"))[:64]
    segment = max(0, _as_int(hello.get("segment"), 0))
    device_label = _as_text(hello.get("device_label"))[:200]
    constraints = hello.get("constraints")
    if not isinstance(constraints, dict):
        constraints = {}

    capture = CaptureInfo(
        source="realtime",
        sample_rate=sample_rate,
        device_label=device_label,
        constraints=constraints,
        session_id=session_id,
        segment=segment,
    )
    analyzer = RealtimeAnalyzer(sample_rate, offset_db=offset_db, full_scale_pa=full_scale_pa)

    recorder: RealtimeRecorder | None = None
    if save_audio:
        recording_path = settings.audio_dir / f"realtime-{uuid.uuid4().hex}.wav"
        try:
            recorder = await asyncio.to_thread(
                RealtimeRecorder,
                recording_path,
                sample_rate,
                settings.max_upload_mb * 1024 * 1024,
            )
        except OSError:
            logger.exception("realtime recorder could not be created")
            await _reject(websocket, "无法创建录音文件，请检查磁盘空间")
            return

    try:
        measurement = await asyncio.to_thread(
            MeasurementRecorder,
            session_id=session_id,
            device_label=device_label,
            sample_rate=sample_rate,
            offset_db=offset_db,
            full_scale_pa=full_scale_pa,
            capture=capture,
        )
    except Exception:
        logger.exception("failed to create measurement record")
        if recorder is not None:
            await asyncio.to_thread(recorder.discard)
        await _reject(websocket, "无法创建测量记录，请检查数据库与磁盘状态")
        return

    await websocket.send_json(
        {
            "type": "ready",
            "sample_rate": sample_rate,
            "fft_size": analyzer.fft_size,
            "channels": 1,
            "dtype": "float32",
            "save_on_stop": save_audio,
            "segment": segment,
            "segments": list(_SESSION_SEGMENTS.get(session_id, [])) if session_id else [],
            "measurement_id": measurement.measurement_id,
        }
    )

    stopped = False
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            chunk = message.get("bytes")
            if chunk:
                samples = np.frombuffer(chunk, dtype="<f4")
                if samples.size:
                    if recorder is not None:
                        await asyncio.to_thread(recorder.write, samples)
                    payload = analyzer.push(samples)
                    await asyncio.to_thread(measurement.record_frame, payload)
                    payload["measurement_id"] = measurement.measurement_id
                    payload["recorded_s"] = measurement.points_written
                    await websocket.send_json(payload)
                continue

            text = message.get("text")
            if not text:
                continue
            try:
                command = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(command, dict):
                continue
            if command.get("type") == "chunk":
                continue
            if command.get("type") == "stop":
                await _finalize(
                    websocket,
                    recorder,
                    analyzer,
                    measurement=measurement,
                    capture=capture,
                    session_id=session_id,
                    offset_db=offset_db,
                    full_scale_pa=full_scale_pa,
                )
                await asyncio.to_thread(measurement.finish, "completed", analyzer.duration_s)
                stopped = True
                break
    except WebSocketDisconnect:
        pass
    finally:
        # 连接被取消（断线/服务关闭）时也要完成归档与收尾，避免录音与测量记录丢失
        with anyio.CancelScope(shield=True):
            if recorder is not None and not recorder.persisted:
                if not stopped and recorder.duration_s >= MIN_SAVE_SECONDS:
                    await _finalize(
                        websocket,
                        recorder,
                        analyzer,
                        measurement=measurement,
                        capture=capture,
                        session_id=session_id,
                        offset_db=offset_db,
                        full_scale_pa=full_scale_pa,
                        interrupted=True,
                    )
                else:
                    await asyncio.to_thread(recorder.discard)
            if not stopped:
                await asyncio.to_thread(measurement.finish, "interrupted", analyzer.duration_s)
            logger.info(
                "realtime websocket closed after %s frame(s), %.2fs audio",
                analyzer.seq,
                analyzer.duration_s,
            )
            await _safe_close(websocket)


async def _finalize(
    websocket: WebSocket,
    recorder: RealtimeRecorder | None,
    analyzer: RealtimeAnalyzer,
    *,
    measurement: MeasurementRecorder,
    capture: CaptureInfo,
    session_id: str,
    offset_db: float,
    full_scale_pa: float,
    interrupted: bool = False,
) -> None:
    """停止采集：未开启录音只回报时长；过短丢弃；否则归档并提交离线分析。"""
    base_payload = {
        "measurement_id": measurement.measurement_id,
        "metric_points": measurement.points_written,
    }
    if recorder is None:
        await _try_send(
            websocket,
            {
                "type": "stopped",
                "duration_s": round(analyzer.duration_s, 3),
                "saved": False,
                **base_payload,
            },
        )
        return

    await asyncio.to_thread(recorder.close)

    if recorder.duration_s < MIN_SAVE_SECONDS:
        await asyncio.to_thread(recorder.discard)
        if not interrupted:
            await _try_send(
                websocket,
                {
                    "type": "discarded",
                    "detail": f"测量时长不足 {MIN_SAVE_SECONDS:g} 秒，本次未保存",
                    **base_payload,
                },
            )
        return

    capture_info = capture.model_copy(update={"interrupted": interrupted})
    try:
        audio_file_id, run_id = await asyncio.to_thread(
            persist_recording,
            recorder,
            offset_db=offset_db,
            full_scale_pa=full_scale_pa,
            capture=capture_info,
        )
    except Exception:
        logger.exception("failed to persist realtime recording")
        await asyncio.to_thread(recorder.discard)
        if not interrupted:
            await _try_send(websocket, {"type": "error", "detail": "保存录音失败，请稍后重试"})
        return

    await asyncio.to_thread(submit_run, run_id)

    segment_payload = {
        "run_id": run_id,
        "audio_file_id": audio_file_id,
        "segment": capture.segment,
        "duration_s": round(recorder.duration_s, 3),
        "truncated": recorder.truncated,
        "interrupted": interrupted,
    }
    await asyncio.to_thread(
        measurement.add_segment,
        segment_index=capture.segment,
        run_id=run_id,
        audio_file_id=audio_file_id,
        duration_s=segment_payload["duration_s"],
        interrupted=interrupted,
    )
    _remember_segment(session_id, segment_payload)

    if not interrupted:
        await _try_send(websocket, {"type": "saved", **segment_payload, **base_payload})


def _remember_segment(session_id: str, payload: dict) -> None:
    if not session_id:
        return
    segments = _SESSION_SEGMENTS.setdefault(session_id, [])
    segments.append(payload)
    while len(_SESSION_SEGMENTS) > SEGMENT_HISTORY_LIMIT:
        _SESSION_SEGMENTS.pop(next(iter(_SESSION_SEGMENTS)))


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


async def _try_send(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except (RuntimeError, WebSocketDisconnect):
        pass


async def _reject(websocket: WebSocket, detail: str) -> None:
    await _try_send(websocket, {"type": "error", "detail": detail})
    await _safe_close(websocket)


async def _safe_close(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except RuntimeError:
        pass
