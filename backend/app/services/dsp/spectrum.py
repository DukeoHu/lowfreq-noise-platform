"""频谱基础算子：Welch PSD、单帧 PSD、短时傅里叶变换。"""

from __future__ import annotations

import numpy as np
from scipy import signal


def next_pow2(value: int) -> int:
    return 1 << max(0, (value - 1).bit_length())


def welch_psd(
    samples: np.ndarray,
    sample_rate: int,
    nperseg: int | None = None,
    overlap: float = 0.5,
) -> tuple[np.ndarray, np.ndarray]:
    """返回 (freqs, psd)，psd 单位为 amplitude^2/Hz。"""
    target = nperseg or min(len(samples), 2**16)
    seg = int(max(256, min(target, len(samples))))
    noverlap = int(seg * overlap)
    freqs, psd = signal.welch(
        samples,
        fs=sample_rate,
        window="hann",
        nperseg=seg,
        noverlap=noverlap,
        detrend="constant",
        scaling="density",
    )
    return freqs, psd


def frame_psd(frame: np.ndarray, sample_rate: int, nfft: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """单帧加窗 PSD，用于时间序列指标。"""
    n = len(frame)
    fft_len = int(nfft or n)
    if fft_len < n:
        raise ValueError("nfft 不能小于帧长")
    window = np.hanning(n)
    spectrum = np.fft.rfft(frame * window, n=fft_len)
    scale = 2.0 / (sample_rate * np.sum(window**2))
    psd = (np.abs(spectrum) ** 2) * scale
    psd[0] /= 2.0
    if fft_len % 2 == 0:
        psd[-1] /= 2.0
    freqs = np.fft.rfftfreq(fft_len, d=1.0 / sample_rate)
    return freqs, psd


def stft_magnitude(
    samples: np.ndarray,
    sample_rate: int,
    nperseg: int = 4096,
    overlap: float = 0.75,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (times, freqs, psd)，psd 为 amplitude^2/Hz。"""
    noverlap = int(nperseg * overlap)
    freqs, times, spec = signal.spectrogram(
        samples,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
        detrend="constant",
        scaling="density",
        mode="psd",
    )
    return times, freqs, spec


def downsample_matrix(matrix: np.ndarray, max_rows: int, max_cols: int) -> tuple[np.ndarray, list[int], list[int]]:
    """按块求均值降采样，返回 (矩阵, 行索引分组, 列索引分组)。"""
    rows, cols = matrix.shape

    def groups(total: int, limit: int) -> list[slice]:
        step = int(np.ceil(total / max(1, limit)))
        return [slice(i, min(i + step, total)) for i in range(0, total, step)]

    row_groups = groups(rows, max_rows)
    col_groups = groups(cols, max_cols)
    reduced = np.empty((len(row_groups), len(col_groups)), dtype=float)
    for i, rs in enumerate(row_groups):
        for j, cs in enumerate(col_groups):
            reduced[i, j] = float(np.mean(matrix[rs, cs]))
    return reduced, row_groups, col_groups
