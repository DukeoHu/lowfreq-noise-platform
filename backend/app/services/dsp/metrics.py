"""声学指标计算：总级、1/3 倍频程、时间序列、低频峰值、声谱图、窄带谱与谐波。"""

from __future__ import annotations

import numpy as np
from scipy import signal

from app.services.dsp import spectrum
from app.services.dsp.bands import THIRD_OCTAVE_CENTERS, band_edges, band_indices, usable_centers
from app.services.dsp.weighting import weight_gain

REFERENCE_PRESSURE_PA = 20e-6


def db_from_ms(mean_square: float) -> float:
    if mean_square <= 0:
        return float("-inf")
    return 10.0 * np.log10(mean_square / REFERENCE_PRESSURE_PA**2)


def _clean(value: float) -> float | None:
    """JSON 不支持 -inf，统一转成 None。"""
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), 1)


def weighted_level(
    psd: np.ndarray,
    freqs: np.ndarray,
    df: float,
    weighting: str,
    offset_db: float = 0.0,
    full_scale_pa: float = 1.0,
) -> float:
    """由 PSD 积分得到计权声级。"""
    gain = weight_gain(freqs, weighting)
    mean_square = float(np.sum(psd * df * gain)) * full_scale_pa**2
    return db_from_ms(mean_square) + offset_db


def overall_levels(
    psd: np.ndarray,
    freqs: np.ndarray,
    df: float,
    offset_db: float = 0.0,
    full_scale_pa: float = 1.0,
) -> dict[str, float | None]:
    laeq = weighted_level(psd, freqs, df, "A", offset_db, full_scale_pa)
    lceq = weighted_level(psd, freqs, df, "C", offset_db, full_scale_pa)
    lz = weighted_level(psd, freqs, df, "Z", offset_db, full_scale_pa)
    return {
        "laeq": _clean(laeq),
        "lceq": _clean(lceq),
        "lzeq": _clean(lz),
        "c_minus_a": _clean(lceq - laeq) if np.isfinite(laeq) and np.isfinite(lceq) else None,
    }


def low_frequency_share(
    psd: np.ndarray,
    freqs: np.ndarray,
    df: float,
    low_hz: float = 20.0,
    high_hz: float = 200.0,
    full_scale_pa: float = 1.0,
) -> dict[str, float | None]:
    """低频段能量占比：20–200 Hz 占全频带的比例，用于快速筛查。"""
    mask = (freqs >= low_hz) & (freqs < high_hz)
    total_ms = float(np.sum(psd * df)) * full_scale_pa**2
    low_ms = float(np.sum(psd[mask] * df)) * full_scale_pa**2
    if total_ms <= 0:
        return {
            "low_hz": low_hz,
            "high_hz": high_hz,
            "ratio": None,
            "low_level_db": None,
            "total_level_db": None,
            "difference_db": None,
        }
    ratio = low_ms / total_ms
    low_db = db_from_ms(low_ms)
    total_db = db_from_ms(total_ms)
    return {
        "low_hz": low_hz,
        "high_hz": high_hz,
        "ratio": round(float(ratio), 4),
        "low_level_db": _clean(low_db),
        "total_level_db": _clean(total_db),
        "difference_db": _clean(low_db - total_db),
    }


def max_and_peak_levels(
    samples: np.ndarray,
    sample_rate: int,
    offset_db: float = 0.0,
    full_scale_pa: float = 1.0,
    fast_s: float = 0.125,
    chunk_s: float = 20.0,
) -> dict[str, float | None]:
    """快速时间计权的最大值（LAFmax/LCFmax）与 C 计权峰值（LCpeak）。

    计权在频域施加：分块做 FFT → 乘计权增益 → IFFT，块间留 0.5 s 重叠区避免边界失真。
    快速计权用 125 ms 指数平均，峰值取 C 计权波形的最大绝对值。
    """
    if len(samples) == 0:
        return {"lafmax": None, "lcfmax": None, "lcpeak": None}

    alpha = float(np.exp(-1.0 / (fast_s * sample_rate)))
    pad = int(0.5 * sample_rate)
    step = max(1, int(chunk_s * sample_rate))
    a_state = 0.0
    c_state = 0.0
    max_a = 0.0
    max_c = 0.0
    peak_c = 0.0

    def _fast_rms(weighted: np.ndarray, state: float) -> tuple[np.ndarray, float]:
        y, _ = signal.lfilter([1.0 - alpha], [1.0, -alpha], weighted**2, zi=[alpha * state])
        return y, float(y[-1])

    for start in range(0, len(samples), step):
        end = min(start + step, len(samples))
        seg_start = max(0, start - pad)
        seg_end = min(len(samples), end + pad)
        seg = samples[seg_start:seg_end]
        n = len(seg)
        freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
        spec = np.fft.rfft(seg)
        inner = slice(start - seg_start, end - seg_start)

        # weight_gain 返回功率增益，时域波形需要开方后的幅度增益
        c_sig = np.fft.irfft(spec * np.sqrt(weight_gain(freqs, "C")), n=n)[inner]
        a_sig = np.fft.irfft(spec * np.sqrt(weight_gain(freqs, "A")), n=n)[inner]

        peak_c = max(peak_c, float(np.max(np.abs(c_sig))))
        a_rms, a_state = _fast_rms(a_sig, a_state)
        c_rms, c_state = _fast_rms(c_sig, c_state)
        max_a = max(max_a, float(np.max(a_rms)))
        max_c = max(max_c, float(np.max(c_rms)))

    lcpeak = 20.0 * np.log10(peak_c * full_scale_pa / REFERENCE_PRESSURE_PA) + offset_db if peak_c > 0 else float("-inf")
    return {
        "lafmax": _clean(db_from_ms(max_a * full_scale_pa**2) + offset_db),
        "lcfmax": _clean(db_from_ms(max_c * full_scale_pa**2) + offset_db),
        "lcpeak": _clean(lcpeak),
    }


def fft_spectrum_payload(
    psd: np.ndarray,
    freqs: np.ndarray,
    df: float,
    full_scale_pa: float = 1.0,
    min_hz: float = 10.0,
    max_hz: float = 500.0,
    max_points: int = 700,
) -> dict[str, object]:
    """窄带 FFT 谱（Z 计权），用于识别具体频率成分。按块取最大值降采样以保留窄峰。"""
    level_db = 10.0 * np.log10(np.maximum(psd * df * full_scale_pa**2, 1e-24) / REFERENCE_PRESSURE_PA**2)
    mask = (freqs >= min_hz) & (freqs <= max_hz)
    f = freqs[mask]
    level = level_db[mask]
    if len(f) == 0:
        return {"f": [], "level": [], "df": round(df, 4), "min_hz": min_hz, "max_hz": max_hz, "window": "hann"}
    if len(f) > max_points:
        step = int(np.ceil(len(f) / max_points))
        centers: list[float] = []
        values: list[float] = []
        for start in range(0, len(f), step):
            block_f = f[start : start + step]
            block_l = level[start : start + step]
            centers.append(float(np.mean(block_f)))
            values.append(float(np.max(block_l)))
        f = np.asarray(centers)
        level = np.asarray(values)
    return {
        "f": [round(float(v), 1) for v in f],
        "level": [round(float(v), 1) for v in level],
        "df": round(df, 4),
        "min_hz": min_hz,
        "max_hz": max_hz,
        "window": "hann",
    }


def harmonic_candidates(
    freqs: np.ndarray,
    psd: np.ndarray,
    df: float,
    max_hz: float = 300.0,
    full_scale_pa: float = 1.0,
    top_n: int = 3,
    max_order: int = 10,
    min_prominence_db: float = 6.0,
    tolerance_ratio: float = 0.01,
) -> list[dict[str, object]]:
    """把窄带谱上的显著峰当作基频，检查 2X–10X 谐波，给出 RPM 与匹配分数。

    仅作为排查线索：匹配分数 = 命中谐波数 / (max_order - 1)。
    """
    level_db = 10.0 * np.log10(np.maximum(psd * df * full_scale_pa**2, 1e-24) / REFERENCE_PRESSURE_PA**2)
    peak_idx, _ = signal.find_peaks(level_db, prominence=min_prominence_db)
    if len(peak_idx) == 0:
        return []

    # 谐波匹配可以使用全频段峰，但基频候选只取低频段
    peak_freqs = freqs[peak_idx]
    peak_levels = level_db[peak_idx]
    candidate_idx = np.where((peak_freqs >= 10.0) & (peak_freqs <= max_hz))[0]
    if len(candidate_idx) == 0:
        return []
    order = candidate_idx[np.argsort(peak_levels[candidate_idx])[::-1]]

    candidates: list[dict[str, object]] = []
    for idx in order[:top_n]:
        fundamental = float(peak_freqs[idx])
        if fundamental <= 0:
            continue
        matched_orders: list[int] = []
        harmonics: list[dict[str, object]] = []
        for k in range(2, max_order + 1):
            target = fundamental * k
            tolerance = max(df, target * tolerance_ratio)
            near = np.abs(peak_freqs - target) <= tolerance
            hit_freq = float(peak_freqs[near][0]) if np.any(near) else None
            if hit_freq is not None:
                matched_orders.append(k)
            harmonics.append(
                {
                    "order": k,
                    "target_hz": round(target, 1),
                    "matched_hz": round(hit_freq, 1) if hit_freq is not None else None,
                }
            )
        candidates.append(
            {
                "fundamental_hz": round(fundamental, 1),
                "fundamental_level_db": round(float(peak_levels[idx]), 1),
                "rpm": round(fundamental * 60.0, 1),
                "matched_orders": matched_orders,
                "score": round(len(matched_orders) / (max_order - 1), 3),
                "harmonics": harmonics,
            }
        )
    candidates.sort(key=lambda item: (item["score"], item["fundamental_level_db"]), reverse=True)
    return candidates


def third_octave_levels(
    psd: np.ndarray,
    freqs: np.ndarray,
    df: float,
    sample_rate: int,
    offset_db: float = 0.0,
    full_scale_pa: float = 1.0,
) -> dict[str, list[dict[str, float | None]]]:
    """分别给出 Z 计权与 A 计权的 1/3 倍频程声级。"""
    centers = usable_centers(sample_rate, fft_resolution_hz=df)
    gains = {"z_weighted": weight_gain(freqs, "Z"), "a_weighted": weight_gain(freqs, "A")}
    result: dict[str, list[dict[str, float | None]]] = {"z_weighted": [], "a_weighted": []}
    for center in centers:
        low, high = band_edges(center)
        mask = band_indices(freqs, low, high)
        if not np.any(mask):
            continue
        band_ms = float(np.sum(psd[mask] * df)) * full_scale_pa**2
        for key, gain in gains.items():
            weighted = band_ms * float(np.mean(gain[mask]))
            result[key].append({"fc": center, "level": _clean(db_from_ms(weighted) + offset_db)})
    return result


def time_series(
    samples: np.ndarray,
    sample_rate: int,
    frame_s: float,
    hop_s: float,
    nfft: int,
    offset_db: float = 0.0,
    full_scale_pa: float = 1.0,
) -> dict[str, list[float] | list[float | None]]:
    """逐帧计算 LAeq / LCeq，用于趋势图。"""
    frame_len = max(256, int(round(frame_s * sample_rate)))
    hop_len = max(1, int(round(hop_s * sample_rate)))
    if len(samples) < frame_len:
        frame_len = len(samples)
    if frame_len <= 0:
        return {"t": [], "laeq": [], "lceq": []}

    times: list[float] = []
    laeq: list[float | None] = []
    lceq: list[float | None] = []

    starts = list(range(0, max(len(samples) - frame_len, 0) + 1, hop_len)) or [0]
    for start in starts:
        frame = samples[start : start + frame_len]
        freqs, psd = spectrum.frame_psd(frame, sample_rate, nfft=max(nfft, spectrum.next_pow2(frame_len)))
        df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.0
        times.append(round((start + frame_len / 2) / sample_rate, 3))
        laeq.append(_clean(weighted_level(psd, freqs, df, "A", offset_db, full_scale_pa)))
        lceq.append(_clean(weighted_level(psd, freqs, df, "C", offset_db, full_scale_pa)))
    return {"t": times, "laeq": laeq, "lceq": lceq}


def low_frequency_peaks(
    band_levels: list[dict[str, float | None]],
    max_hz: float = 300.0,
    top_n: int = 5,
    min_prominence_db: float = 1.0,
    max_level_drop_db: float = 25.0,
) -> list[dict[str, float | None]]:
    """在 1/3 倍频程谱上查找低频局部峰值。"""
    candidates = [(i, b) for i, b in enumerate(band_levels) if b["fc"] <= max_hz and b["level"] is not None]
    if len(candidates) < 3:
        return []
    peaks: list[dict[str, float | None]] = []
    for pos, (_idx, band) in enumerate(candidates):
        if pos == 0 or pos == len(candidates) - 1:
            continue
        prev_level = candidates[pos - 1][1]["level"]
        next_level = candidates[pos + 1][1]["level"]
        level = band["level"]
        if prev_level is None or next_level is None or level is None:
            continue
        if level >= prev_level + min_prominence_db and level >= next_level + min_prominence_db:
            peaks.append({"fc": band["fc"], "level": level, "prominence": round(level - max(prev_level, next_level), 1)})
    if not peaks:
        return []
    # 与最强峰相差过大的局部起伏（如直流泄漏引起的 4 Hz 起伏）不视为可排查的峰
    strongest = max(peak["level"] for peak in peaks if peak["level"] is not None)
    peaks = [peak for peak in peaks if peak["level"] is not None and peak["level"] >= strongest - max_level_drop_db]
    peaks.sort(key=lambda item: item["level"] or -1e9, reverse=True)
    return peaks[:top_n]


def spectrogram_payload(
    samples: np.ndarray,
    sample_rate: int,
    nperseg: int,
    max_time_bins: int,
    max_freq_bins: int,
    max_freq_hz: float,
    full_scale_pa: float = 1.0,
) -> dict[str, object]:
    """降采样后的声谱图矩阵，避免浏览器内存失控。"""
    times, freqs, psd = spectrum.stft_magnitude(samples, sample_rate, nperseg=nperseg)
    df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 1.0
    db_matrix = 10.0 * np.log10(np.maximum(psd * df * full_scale_pa**2, 1e-20) / REFERENCE_PRESSURE_PA**2)
    freq_mask = freqs <= max_freq_hz
    db_matrix = db_matrix[freq_mask, :]
    freqs = freqs[freq_mask]
    reduced, _, _ = spectrum.downsample_matrix(db_matrix, max_freq_bins, max_time_bins)
    time_idx = np.linspace(0, len(times) - 1, reduced.shape[1]).astype(int) if len(times) else np.array([], dtype=int)
    freq_idx = np.linspace(0, len(freqs) - 1, reduced.shape[0]).astype(int) if len(freqs) else np.array([], dtype=int)
    return {
        "t": [round(float(times[i]), 2) for i in time_idx],
        "f": [round(float(freqs[i]), 1) for i in freq_idx],
        "z": [[round(float(v), 1) for v in row] for row in reduced],
        "vmin": round(float(np.percentile(reduced, 5)), 1),
        "vmax": round(float(np.percentile(reduced, 99)), 1),
    }


def band_centers_for_display(sample_rate: int, df: float) -> list[float]:
    return usable_centers(sample_rate, fft_resolution_hz=df)


def all_third_octave_centers() -> tuple[float, ...]:
    return THIRD_OCTAVE_CENTERS
