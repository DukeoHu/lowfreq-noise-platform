"""实时采集分析：按块接收 float32 单声道采样，输出窗口声级、累计声级与低频谱。

与离线流水线共用同一套 dsp 模块（frame_psd / overall_levels / third_octave_levels），
因此同一段信号在实时与离线两条链路上的读数保持一致。

时间计权（LAF/LCF）与峰值需要无分块痕迹的计权波形，这里用带前后文的重叠保留法：
每个 hop 都取历史 8192 点 + 当前 4096 点 + 未来 4096 点一起做线性卷积，
只保留中间 hop 的输出，避免块边界阶跃在计权后产生宽带伪峰。
"""

from __future__ import annotations

import numpy as np
from scipy import signal

from app.services.dsp import metrics, spectrum
from app.services.dsp.weighting import weight_gain

DEFAULT_FFT_SIZE = 8192
FAST_TIME_CONSTANT_S = 0.125
FFT_MIN_HZ = 10.0
FFT_MAX_HZ = 500.0
FFT_MAX_POINTS = 200
BAND_MIN_HZ = 20.0
BAND_MAX_HZ = 200.0


def _clean(value: float) -> float | None:
    if not np.isfinite(value):
        return None
    return round(float(value), 1)


class RealtimeAnalyzer:
    """维护最近一个分析窗口，并按块累加能量。"""

    def __init__(
        self,
        sample_rate: int,
        offset_db: float = 0.0,
        full_scale_pa: float = 1.0,
        fft_size: int = DEFAULT_FFT_SIZE,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.offset_db = float(offset_db)
        self.full_scale_pa = float(full_scale_pa)
        self.fft_size = int(fft_size)
        self._hop = max(1024, self.fft_size // 2)
        self._lookahead = self._hop

        self._tail = np.zeros(0, dtype=float)
        self._pending = np.zeros(0, dtype=float)
        self._history = np.zeros(0, dtype=float)
        self._freq_cache: dict[int, np.ndarray] = {}
        self._alpha = float(np.exp(-1.0 / (FAST_TIME_CONSTANT_S * self.sample_rate)))
        self._laf_ms = 0.0
        self._lcf_ms = 0.0
        self._laf_max_ms = 0.0
        self._lcf_max_ms = 0.0
        self._peak_c = 0.0
        self._peak_hold: np.ndarray | None = None
        self._hold_f: list[float] = []
        self._energy = {"z": 0.0, "a": 0.0, "c": 0.0}
        self._samples = 0
        self._processed = 0
        self._seq = 0
        self._elapsed = 0.0

        # 逐秒指标：按整秒对 A/C 计权波形做能量积分，避免把 171 ms 窗口快照当作 1 秒 Leq
        self._second_energy = {"a": 0.0, "c": 0.0}
        self._second_samples = 0
        self._second_laf_max_ms = 0.0
        self._second_lcf_max_ms = 0.0
        self._second_laf_end_ms = 0.0
        self._second_lcf_end_ms = 0.0
        self._second_peak_c = 0.0
        self._next_second = 0
        self._seconds: list[dict] = []
        self._z_seconds: dict[int, list[float]] = {}

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def duration_s(self) -> float:
        return self._elapsed

    def push(self, chunk: np.ndarray) -> dict:
        """处理一块采样并返回可直接 JSON 序列化的一帧指标。"""
        data = np.asarray(chunk, dtype=np.float64).ravel()
        if data.size == 0:
            return self._payload(None)

        self._seq += 1
        self._elapsed += data.size / self.sample_rate
        start_sample = self._samples
        self._samples += data.size
        self._energy["z"] += float(np.sum(data**2))
        self._accumulate_z_seconds(data, start_sample)

        self._pending = np.concatenate([self._pending, data])
        while self._pending.size >= self._hop + self._lookahead:
            self._process_hop()

        self._tail = np.concatenate([self._tail, data])[-self.fft_size :]
        window = self._window_metrics() if self._tail.size >= self.fft_size else None
        return self._payload(window)

    # ------------------------------------------------------------------ 内部实现

    def _freqs_for(self, n: int) -> np.ndarray:
        freqs = self._freq_cache.get(n)
        if freqs is None:
            freqs = np.fft.rfftfreq(n, d=1.0 / self.sample_rate)
            self._freq_cache[n] = freqs
        return freqs

    def _process_hop(self) -> None:
        """对最早的一个 hop 施加 A/C 计权，累加能量并更新快速计权与峰值。"""
        hop = self._hop
        context = self._history
        block = self._pending[:hop]
        lookahead = self._pending[hop : hop + self._lookahead]
        combined = np.concatenate([context, block, lookahead])

        length = spectrum.next_pow2(combined.size + self.fft_size)
        freqs = self._freqs_for(length)
        spec = np.fft.rfft(combined, n=length)
        valid = slice(context.size, context.size + hop)
        a_sig = np.fft.irfft(spec * np.sqrt(weight_gain(freqs, "A")), n=length)[valid]
        c_sig = np.fft.irfft(spec * np.sqrt(weight_gain(freqs, "C")), n=length)[valid]

        self._energy["a"] += float(np.sum(a_sig**2))
        self._energy["c"] += float(np.sum(c_sig**2))
        self._peak_c = max(self._peak_c, float(np.max(np.abs(c_sig))))
        y_a, self._laf_ms = self._fast_rms_series(a_sig, self._laf_ms)
        y_c, self._lcf_ms = self._fast_rms_series(c_sig, self._lcf_ms)
        self._laf_max_ms = max(self._laf_max_ms, float(np.max(y_a)))
        self._lcf_max_ms = max(self._lcf_max_ms, float(np.max(y_c)))
        self._accumulate_second(a_sig, c_sig, y_a, y_c)

        self._history = np.concatenate([context, block])[-self.fft_size :]
        self._pending = self._pending[hop:]
        self._processed += hop

    def _fast_rms_series(self, weighted: np.ndarray, state: float) -> tuple[np.ndarray, float]:
        """返回本 hop 的快速计权均方序列，以及供下一 hop 使用的最终状态。"""
        y, _ = signal.lfilter(
            [1.0 - self._alpha],
            [1.0, -self._alpha],
            weighted**2,
            zi=[self._alpha * state],
        )
        return y, float(y[-1])

    def _accumulate_z_seconds(self, data: np.ndarray, start_sample: int) -> None:
        """按原始采样把 Z 计权能量切分到整秒，供逐秒 LZeq 使用。"""
        pos = 0
        size = int(data.size)
        while pos < size:
            second = (start_sample + pos) // self.sample_rate
            boundary = (second + 1) * self.sample_rate
            take = min(size - pos, boundary - (start_sample + pos))
            entry = self._z_seconds.setdefault(second, [0.0, 0.0])
            entry[0] += float(np.sum(data[pos : pos + take] ** 2))
            entry[1] += take
            pos += take

    def _accumulate_second(
        self,
        a_sig: np.ndarray,
        c_sig: np.ndarray,
        y_a: np.ndarray,
        y_c: np.ndarray,
    ) -> None:
        """把 hop 输出按整秒边界切开，跨秒时落一条该秒的真实指标。"""
        hop = int(a_sig.size)
        pos = 0
        while pos < hop:
            boundary = (self._next_second + 1) * self.sample_rate
            remaining = boundary - (self._processed + pos)
            if remaining <= 0:
                self._finalize_second()
                continue
            take = min(hop - pos, remaining)
            seg_a = a_sig[pos : pos + take]
            seg_c = c_sig[pos : pos + take]
            self._second_energy["a"] += float(np.sum(seg_a**2))
            self._second_energy["c"] += float(np.sum(seg_c**2))
            self._second_peak_c = max(self._second_peak_c, float(np.max(np.abs(seg_c))))
            self._second_laf_max_ms = max(
                self._second_laf_max_ms, float(np.max(y_a[pos : pos + take]))
            )
            self._second_lcf_max_ms = max(
                self._second_lcf_max_ms, float(np.max(y_c[pos : pos + take]))
            )
            self._second_laf_end_ms = float(y_a[pos + take - 1])
            self._second_lcf_end_ms = float(y_c[pos + take - 1])
            self._second_samples += take
            pos += take
            if take == remaining:
                self._finalize_second()

    def _finalize_second(self) -> None:
        """输出一个完整秒的真实 Leq、秒内 LAFmax/LCFmax/LCpeak 与秒末 LAF/LCF。"""
        index = self._next_second
        samples = self._second_samples
        z_entry = self._z_seconds.pop(index, None)
        laeq = self._mean_square_level(self._second_energy["a"], samples)
        lceq = self._mean_square_level(self._second_energy["c"], samples)
        lzeq = (
            self._mean_square_level(z_entry[0], int(z_entry[1]))
            if z_entry is not None and z_entry[1] > 0
            else None
        )
        self._seconds.append(
            {
                "t_offset_s": index,
                "laeq": laeq,
                "lceq": lceq,
                "lzeq": lzeq,
                "c_minus_a": _clean(lceq - laeq) if laeq is not None and lceq is not None else None,
                "laf": self._fast_level(self._second_laf_end_ms),
                "lcf": self._fast_level(self._second_lcf_end_ms),
                "lafmax": self._fast_level(self._second_laf_max_ms),
                "lcfmax": self._fast_level(self._second_lcf_max_ms),
                "lcpeak": self._peak_level_from(self._second_peak_c),
            }
        )
        self._next_second += 1
        self._second_energy = {"a": 0.0, "c": 0.0}
        self._second_samples = 0
        self._second_laf_max_ms = 0.0
        self._second_lcf_max_ms = 0.0
        self._second_laf_end_ms = 0.0
        self._second_lcf_end_ms = 0.0
        self._second_peak_c = 0.0

    def _drain_seconds(self) -> list[dict]:
        if not self._seconds:
            return []
        seconds = self._seconds
        self._seconds = []
        return seconds

    def _window_metrics(self) -> dict:
        freqs, psd = spectrum.frame_psd(self._tail, self.sample_rate, nfft=self.fft_size)
        df = float(freqs[1] - freqs[0])
        overall = metrics.overall_levels(psd, freqs, df, self.offset_db, self.full_scale_pa)
        fft_payload = metrics.fft_spectrum_payload(
            psd,
            freqs,
            df,
            self.full_scale_pa,
            min_hz=FFT_MIN_HZ,
            max_hz=FFT_MAX_HZ,
            max_points=FFT_MAX_POINTS,
        )
        bands = metrics.third_octave_levels(
            psd, freqs, df, self.sample_rate, self.offset_db, self.full_scale_pa
        )["z_weighted"]

        level = np.asarray(fft_payload["level"], dtype=float)
        if self._peak_hold is None or self._peak_hold.size != level.size:
            self._peak_hold = level.copy()
            self._hold_f = list(fft_payload["f"])
        else:
            self._peak_hold = np.maximum(self._peak_hold, level)

        return {
            "overall": overall,
            "fft": {"f": fft_payload["f"], "level": fft_payload["level"]},
            "spectrum": {"f": fft_payload["f"], "level": fft_payload["level"]},
            "peak_hold": {"f": self._hold_f, "level": [round(float(v), 1) for v in self._peak_hold]},
            "bands": [band for band in bands if BAND_MIN_HZ <= band["fc"] <= BAND_MAX_HZ],
        }

    def _cumulative(self) -> dict:
        return {
            "laeq": self._mean_square_level(self._energy["a"], self._processed),
            "lceq": self._mean_square_level(self._energy["c"], self._processed),
            "lzeq": self._mean_square_level(self._energy["z"], self._samples),
            "duration_s": round(self._elapsed, 3),
        }

    def _mean_square_level(self, energy: float, samples: int) -> float | None:
        """由能量和样本数得到等效声级。"""
        if samples <= 0 or energy <= 0:
            return None
        mean_square = energy / samples * self.full_scale_pa**2
        return _clean(metrics.db_from_ms(mean_square) + self.offset_db)

    def _peak_level(self) -> float | None:
        return self._peak_level_from(self._peak_c)

    def _peak_level_from(self, peak: float) -> float | None:
        if peak <= 0:
            return None
        level = 20.0 * np.log10(peak * self.full_scale_pa / metrics.REFERENCE_PRESSURE_PA)
        return _clean(level + self.offset_db)

    def _fast_level(self, mean_square: float) -> float | None:
        if mean_square <= 0:
            return None
        return _clean(metrics.db_from_ms(mean_square * self.full_scale_pa**2) + self.offset_db)

    def _payload(self, window: dict | None) -> dict:
        levels: dict[str, float | None] = {
            "laeq": None,
            "lceq": None,
            "lzeq": None,
            "c_minus_a": None,
        }
        if window is not None:
            levels.update(window["overall"])

        levels["laf"] = self._fast_level(self._laf_ms)
        levels["lcf"] = self._fast_level(self._lcf_ms)
        levels["lafmax"] = self._fast_level(self._laf_max_ms)
        levels["lcfmax"] = self._fast_level(self._lcf_max_ms)
        levels["lcpeak"] = self._peak_level()

        return {
            "type": "frame",
            "seq": self._seq,
            "ready": window is not None,
            "t": round(self._elapsed, 3),
            "sample_rate": self.sample_rate,
            "levels": levels,
            "seconds": self._drain_seconds(),
            "cumulative": self._cumulative(),
            "fft": window["fft"] if window else {"f": [], "level": []},
            "spectrum": window["spectrum"] if window else {"f": [], "level": []},
            "peak_hold": window["peak_hold"] if window else {"f": [], "level": []},
            "bands": window["bands"] if window else [],
        }
