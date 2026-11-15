"""IEC 61672 频率计权（A / C / Z）。

在频域按频点施加计权增益，保证同一份音频在任意机器上得到完全一致的离线结果。
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-12


def a_weight_db(freq_hz: np.ndarray | float) -> np.ndarray:
    """A 计权（dB），0 dB 参考点在 1 kHz。"""
    f = np.asarray(freq_hz, dtype=float)
    f2 = f**2
    num = 12194.0**2 * f2**2
    den = (f2 + 20.6**2) * np.sqrt((f2 + 107.7**2) * (f2 + 737.9**2)) * (f2 + 12194.0**2)
    ratio = np.divide(num, den, out=np.zeros_like(f), where=den > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 20.0 * np.log10(np.maximum(ratio, _EPS)) + 2.0
    return np.where(ratio > 0, db, -200.0)


def c_weight_db(freq_hz: np.ndarray | float) -> np.ndarray:
    """C 计权（dB），0 dB 参考点在 1 kHz。"""
    f = np.asarray(freq_hz, dtype=float)
    f2 = f**2
    num = 12194.0**2 * f2
    den = (f2 + 20.6**2) * (f2 + 12194.0**2)
    ratio = np.divide(num, den, out=np.zeros_like(f), where=den > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 20.0 * np.log10(np.maximum(ratio, _EPS)) + 0.06
    return np.where(ratio > 0, db, -200.0)


WEIGHTINGS = {
    "A": a_weight_db,
    "C": c_weight_db,
    "Z": lambda freq: np.zeros_like(np.asarray(freq, dtype=float)),
}


def weight_gain(freq_hz: np.ndarray, weighting: str) -> np.ndarray:
    """返回功率域增益（线性倍数），用于直接乘到 PSD 上。"""
    try:
        func = WEIGHTINGS[weighting.upper()]
    except KeyError as exc:
        raise ValueError(f"不支持的计权类型: {weighting}") from exc
    return 10.0 ** (func(freq_hz) / 10.0)
