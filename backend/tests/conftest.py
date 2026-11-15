from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="lowfreq-test-"))
os.environ["LOWFREQ_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["LOWFREQ_DATABASE_URL"] = f"sqlite:///{(_TEST_DATA_DIR / 'test.db').as_posix()}"

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def wav_factory(tmp_path: Path):
    """生成指定频率/幅值的单声道 WAV，用于黄金数据与接口测试。"""

    def _make(
        freq_hz: float,
        amplitude: float = 0.1,
        duration_s: float = 5.0,
        sample_rate: int = 48000,
        name: str | None = None,
    ) -> Path:
        t = np.arange(int(duration_s * sample_rate)) / sample_rate
        data = (amplitude * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
        path = tmp_path / (name or f"tone-{int(freq_hz)}.wav")
        sf.write(str(path), data, sample_rate, subtype="FLOAT")
        return path

    return _make
