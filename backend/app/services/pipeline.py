"""分析流水线：一个音频文件 + 一份配置 = 一个可复现的 AnalysisRun。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.schemas.analysis import AnalysisConfig
from app.services import audio_io
from app.services.dsp import metrics, quality, spectrum

ALGORITHM_VERSION = "dsp-mvp-0.1.0"


def run_analysis(audio_path: Path, config: AnalysisConfig) -> dict:
    samples, sample_rate, channels = audio_io.load_mono(
        audio_path, max_seconds=get_settings().analysis_max_seconds
    )

    calibration = config.calibration
    offset_db = calibration.offset_db
    full_scale_pa = calibration.full_scale_pa

    quality_report = quality.analyze_quality(samples, sample_rate, channels, calibration.is_calibrated)

    freqs, psd = spectrum.welch_psd(samples, sample_rate, nperseg=config.fft_size)
    df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.0

    overall = metrics.overall_levels(psd, freqs, df, offset_db, full_scale_pa)
    overall.update(metrics.max_and_peak_levels(samples, sample_rate, offset_db, full_scale_pa))
    low_frequency = metrics.low_frequency_share(psd, freqs, df, full_scale_pa=full_scale_pa)
    fft_payload = metrics.fft_spectrum_payload(psd, freqs, df, full_scale_pa)
    harmonics = metrics.harmonic_candidates(freqs, psd, df, max_hz=config.peak_max_hz, full_scale_pa=full_scale_pa)
    bands = metrics.third_octave_levels(psd, freqs, df, sample_rate, offset_db, full_scale_pa)
    series = metrics.time_series(
        samples,
        sample_rate,
        frame_s=config.frame_s,
        hop_s=config.hop_s,
        nfft=config.fft_size,
        offset_db=offset_db,
        full_scale_pa=full_scale_pa,
    )
    peaks = metrics.low_frequency_peaks(bands["z_weighted"], config.peak_max_hz, config.peak_top_n)

    spectrogram = None
    if config.include_spectrogram:
        spectrogram = metrics.spectrogram_payload(
            samples,
            sample_rate,
            nperseg=config.spectrogram_nperseg,
            max_time_bins=config.spectrogram_max_time_bins,
            max_freq_bins=config.spectrogram_max_freq_bins,
            max_freq_hz=config.spectrogram_max_freq_hz,
            full_scale_pa=full_scale_pa,
        )

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audio": {
            "sample_rate": sample_rate,
            "channels": channels,
            "samples": int(len(samples)),
            "duration_s": round(len(samples) / sample_rate, 3) if sample_rate else 0.0,
        },
        "calibration": {
            "mode": calibration.mode,
            "offset_db": offset_db,
            "full_scale_pa": full_scale_pa,
        },
        "overall": overall,
        "low_frequency": low_frequency,
        "fft": fft_payload,
        "harmonics": harmonics,
        "bands": bands,
        "time_series": series,
        "peaks": peaks,
        "spectrogram": spectrogram,
        "quality": quality_report,
    }
