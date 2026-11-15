"""1/3 倍频程频带定义（IEC 61260 标称中心频率）。"""

from __future__ import annotations

import numpy as np

# 1 Hz - 20 kHz 的标称 1/3 倍频程中心频率
THIRD_OCTAVE_CENTERS: tuple[float, ...] = (
    1.0, 1.25, 1.6, 2.0, 2.5, 3.15, 4.0, 5.0, 6.3, 8.0, 10.0, 12.5, 16.0, 20.0,
    25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0, 250.0, 315.0,
    400.0, 500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0, 2000.0, 2500.0, 3150.0,
    4000.0, 5000.0, 6300.0, 8000.0, 10000.0, 12500.0, 16000.0, 20000.0,
)

# 低频排查默认关注的上限
LOW_FREQUENCY_LIMIT_HZ = 200.0


def band_edges(center_hz: float) -> tuple[float, float]:
    """返回频带上下限（中心频率 ± 1/6 倍频程）。"""
    return center_hz * 2.0 ** (-1.0 / 6.0), center_hz * 2.0 ** (1.0 / 6.0)


def band_indices(freqs: np.ndarray, low_hz: float, high_hz: float) -> np.ndarray:
    """布尔掩码：位于 [low_hz, high_hz) 的频点。"""
    return (freqs >= low_hz) & (freqs < high_hz)


def usable_centers(sample_rate: int, fft_resolution_hz: float | None = None) -> list[float]:
    """过滤掉超出可用频率范围（< Nyquist）的频带中心。"""
    nyquist = sample_rate / 2.0
    centers = [c for c in THIRD_OCTAVE_CENTERS if c * 2.0 ** (1.0 / 6.0) <= nyquist]
    if fft_resolution_hz:
        # 带宽窄于一个 FFT bin 时该频带不可信
        centers = [c for c in centers if 2.0 ** (1.0 / 3.0) * c / 2.0 >= fft_resolution_hz]
    return centers
