"""音频质量标志：把"能不能信这个结果"显式记录下来。"""

from __future__ import annotations

import numpy as np


def analyze_quality(
    samples: np.ndarray,
    sample_rate: int,
    channels: int,
    calibrated: bool,
) -> dict[str, object]:
    duration = len(samples) / sample_rate if sample_rate else 0.0
    abs_samples = np.abs(samples)
    clip_ratio = float(np.mean(abs_samples >= 0.999)) if len(samples) else 0.0
    dc_offset = float(np.mean(samples)) if len(samples) else 0.0
    rms = float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0
    rms_dbfs = float(20.0 * np.log10(max(rms, 1e-12)))

    frames = max(1, int(duration / 0.1))
    if len(samples) >= frames:
        chunk = len(samples) // frames
        frame_rms = [float(np.sqrt(np.mean(samples[i * chunk : (i + 1) * chunk] ** 2) or 0.0)) for i in range(frames)]
        silence_ratio = float(np.mean([r <= 1e-5 for r in frame_rms]))
    else:
        silence_ratio = 0.0 if rms > 1e-5 else 1.0

    flags: list[str] = []
    if not calibrated:
        flags.append("uncalibrated")
    if clip_ratio > 1e-4:
        flags.append("clipping_suspected")
    if abs(dc_offset) > 1e-3:
        flags.append("dc_offset_detected")
    if rms_dbfs < -90.0:
        flags.append("digital_silence")
    if silence_ratio > 0.9:
        flags.append("mostly_silent")
    if duration < 10.0:
        flags.append("short_duration")
    if channels > 1:
        flags.append("multichannel_downmixed")

    return {
        "duration_s": round(duration, 3),
        "sample_rate": sample_rate,
        "channels": channels,
        "calibrated": calibrated,
        "clip_ratio": round(clip_ratio, 6),
        "dc_offset": round(dc_offset, 6),
        "rms_dbfs": round(rms_dbfs, 1),
        "silence_ratio": round(silence_ratio, 3),
        "flags": flags,
    }
