"""音频读取与基础探测（MVP 以离线文件分析为主）。

解码按块读入并直接写入预分配的单声道缓冲，避免整段 float64 音频与 downmix 副本同时驻留；
同时用时长上限约束后续 FFT/频谱计算的内存峰值。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# 每块约 1M 帧：48 kHz 下约 22 s，兼顾内存与循环次数
CHUNK_FRAMES = 1 << 20


class AudioDecodeError(RuntimeError):
    pass


class AudioTooLongError(AudioDecodeError):
    pass


def probe_audio(path: Path) -> dict[str, object]:
    """只读取头部信息，避免大文件全量解码。"""
    import soundfile as sf

    try:
        info = sf.info(str(path))
    except Exception as exc:  # soundfile 抛出的异常类型不固定
        raise AudioDecodeError(f"无法解析音频文件: {exc}") from exc
    return {
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "frames": int(info.frames),
        "duration_s": round(float(info.frames) / float(info.samplerate), 3) if info.samplerate else 0.0,
        "format": info.format,
        "subtype": info.subtype,
    }


def ensure_duration_allowed(duration_s: float | None, max_seconds: int) -> None:
    """超出分析时长上限时尽早拒绝，避免解码与频谱计算耗尽内存。"""
    if max_seconds and max_seconds > 0 and duration_s and duration_s > max_seconds:
        raise AudioTooLongError(
            f"音频时长 {duration_s:.0f} 秒超过分析上限 {max_seconds} 秒；"
            "请裁剪后重试，或由管理员调整 LOWFREQ_ANALYSIS_MAX_SECONDS"
        )


def load_mono(
    path: Path,
    *,
    max_seconds: int = 0,
    chunk_frames: int = CHUNK_FRAMES,
) -> tuple[np.ndarray, int, int]:
    """分块读取为单声道 float64 数组，返回 (samples, sample_rate, 原始声道数)。

    单块按 float32 读取，downmix 后写入 float64 缓冲：既保持 DSP 精度，
    也避免旧实现中「整段 float64 立体声 + 单声道副本」的双份全量内存。
    """
    import soundfile as sf

    try:
        info = sf.info(str(path))
    except Exception as exc:
        raise AudioDecodeError(f"无法解码音频文件: {exc}") from exc

    channels = int(info.channels)
    sample_rate = int(info.samplerate)
    total_frames = int(info.frames)
    ensure_duration_allowed(total_frames / sample_rate if sample_rate else 0.0, max_seconds)

    mono = np.empty(max(total_frames, 0), dtype=np.float64)
    written = 0
    try:
        with sf.SoundFile(str(path)) as handle:
            while True:
                block = handle.read(frames=chunk_frames, dtype="float32", always_2d=True)
                if block.shape[0] == 0:
                    break
                piece = block.mean(axis=1, dtype=np.float64) if channels > 1 else block[:, 0]
                mono[written : written + block.shape[0]] = piece
                written += block.shape[0]
    except Exception as exc:
        raise AudioDecodeError(f"无法解码音频文件: {exc}") from exc

    if written != total_frames:
        mono = mono[:written]
    return np.ascontiguousarray(mono), sample_rate, channels
