from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from app.services import audio_io


def test_load_mono_chunks_and_downmixes(tmp_path) -> None:
    sample_rate = 48000
    t = np.arange(sample_rate) / sample_rate
    left = 0.5 * np.sin(2 * np.pi * 100 * t)
    right = 0.1 * np.sin(2 * np.pi * 100 * t)
    stereo = np.stack([left, right], axis=1).astype(np.float32)
    path = tmp_path / "stereo.wav"
    sf.write(str(path), stereo, sample_rate, subtype="FLOAT")

    mono, sr, channels = audio_io.load_mono(path, chunk_frames=4096)

    assert sr == sample_rate
    assert channels == 2
    assert mono.dtype == np.float64
    assert mono.shape == (sample_rate,)
    expected = (left + right) / 2
    assert float(np.max(np.abs(mono - expected))) < 1e-6


def test_load_mono_rejects_audio_over_limit(wav_factory) -> None:
    path = wav_factory(63, duration_s=3.0, name="over-limit.wav")

    with pytest.raises(audio_io.AudioTooLongError):
        audio_io.load_mono(path, max_seconds=1)


def test_ensure_duration_allowed_passes_without_limit() -> None:
    audio_io.ensure_duration_allowed(3600.0, 0)
    audio_io.ensure_duration_allowed(None, 60)
