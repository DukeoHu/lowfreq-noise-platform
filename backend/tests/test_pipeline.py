from __future__ import annotations

import numpy as np
import pytest

from app.schemas.analysis import AnalysisConfig
from app.services.pipeline import ALGORITHM_VERSION, run_analysis


def _expected_tone_level(amplitude: float) -> float:
    """正弦波理论声级：20*log10((A/sqrt(2))/20e-6)。"""
    return 20.0 * np.log10((amplitude / np.sqrt(2)) / 20e-6)


def test_1khz_tone_matches_theory(wav_factory) -> None:
    path = wav_factory(freq_hz=1000, amplitude=0.1, duration_s=5.0)
    result = run_analysis(path, AnalysisConfig(include_spectrogram=False))

    expected = _expected_tone_level(0.1)
    assert result["overall"]["lzeq"] == pytest.approx(expected, abs=0.5)
    # 1 kHz 处 A/C 计权均为 0 dB
    assert result["overall"]["laeq"] == pytest.approx(expected, abs=0.5)
    assert result["overall"]["lceq"] == pytest.approx(expected, abs=0.5)
    assert result["overall"]["c_minus_a"] == pytest.approx(0.0, abs=0.3)


def test_63hz_tone_a_weighting_and_band(wav_factory) -> None:
    path = wav_factory(freq_hz=63, amplitude=0.1, duration_s=5.0)
    result = run_analysis(path, AnalysisConfig(include_spectrogram=False))

    z_expected = _expected_tone_level(0.1)
    assert result["overall"]["lzeq"] == pytest.approx(z_expected, abs=0.5)
    # A 计权在 63 Hz 约 -26.2 dB
    assert result["overall"]["laeq"] == pytest.approx(z_expected - 26.2, abs=1.0)

    band_63 = next(b for b in result["bands"]["z_weighted"] if b["fc"] == 63)
    assert band_63["level"] == pytest.approx(z_expected, abs=1.0)
    # 能量应集中在 63 Hz 频带
    neighbors = [b for b in result["bands"]["z_weighted"] if b["fc"] in (50.0, 80.0)]
    for band in neighbors:
        assert band["level"] < band_63["level"] - 10.0


def test_low_frequency_peak_detected(wav_factory) -> None:
    path = wav_factory(freq_hz=63, amplitude=0.1, duration_s=5.0)
    result = run_analysis(path, AnalysisConfig(include_spectrogram=False, peak_top_n=3))

    assert result["peaks"], "应检测到低频峰值"
    assert result["peaks"][0]["fc"] == 63
    assert result["algorithm_version"] == ALGORITHM_VERSION


def test_time_series_and_spectrogram_shape(wav_factory) -> None:
    path = wav_factory(freq_hz=63, amplitude=0.1, duration_s=6.0)
    result = run_analysis(path, AnalysisConfig())

    series = result["time_series"]
    assert series["t"] and len(series["t"]) == len(series["laeq"])
    assert all(value is not None and value > 0 for value in series["laeq"])

    spec = result["spectrogram"]
    assert spec is not None
    assert len(spec["z"]) <= 256
    assert all(len(row) <= 400 for row in spec["z"])
    assert len(spec["z"]) == len(spec["f"])


def test_quality_flags_uncalibrated(wav_factory) -> None:
    path = wav_factory(freq_hz=63, amplitude=0.1, duration_s=5.0)
    result = run_analysis(path, AnalysisConfig(include_spectrogram=False))

    quality = result["quality"]
    assert quality["calibrated"] is False
    assert "uncalibrated" in quality["flags"]
    assert quality["clip_ratio"] == pytest.approx(0.0, abs=1e-6)


def test_short_and_silent_audio_flagged(tmp_path) -> None:
    import soundfile as sf

    path = tmp_path / "silence.wav"
    sf.write(str(path), np.zeros(4800, dtype=np.float32), 48000, subtype="FLOAT")
    result = run_analysis(path, AnalysisConfig(include_spectrogram=False))

    assert result["overall"]["laeq"] is None
    assert "digital_silence" in result["quality"]["flags"]
    assert "short_duration" in result["quality"]["flags"]
