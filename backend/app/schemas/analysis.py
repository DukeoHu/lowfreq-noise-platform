from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CalibrationConfig(BaseModel):
    """校准信息。未校准时结果只作为相对值展示。"""

    mode: Literal["uncalibrated", "calibrated"] = "uncalibrated"
    offset_db: float = Field(default=0.0, ge=-60.0, le=60.0)
    full_scale_pa: float = Field(default=1.0, gt=0.0, description="数字满量程对应的声压(Pa)")

    @property
    def is_calibrated(self) -> bool:
        return self.mode == "calibrated"


class CaptureInfo(BaseModel):
    """采集环境信息，用于结果追溯（上传或实时）。"""

    source: Literal["upload", "realtime"] = "upload"
    sample_rate: int | None = None
    device_label: str = ""
    constraints: dict = {}
    session_id: str = ""
    segment: int = 0
    interrupted: bool = False


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_s: float = Field(default=1.0, gt=0.05, le=60.0, description="时间序列帧长(秒)")
    hop_s: float = Field(default=0.5, gt=0.01, le=60.0, description="时间序列步长(秒)")
    fft_size: int = Field(default=65536, ge=1024, le=2**20, description="Welch/帧 PSD 使用的 FFT 点数")
    include_spectrogram: bool = True
    spectrogram_nperseg: int = Field(default=4096, ge=512, le=65536)
    spectrogram_max_time_bins: int = Field(default=400, ge=50, le=2000)
    spectrogram_max_freq_bins: int = Field(default=256, ge=32, le=1024)
    spectrogram_max_freq_hz: float = Field(default=2000.0, gt=0.0)
    peak_max_hz: float = Field(default=300.0, gt=0.0)
    peak_top_n: int = Field(default=5, ge=1, le=20)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    capture: CaptureInfo | None = None


class AnalysisCreate(BaseModel):
    audio_file_id: str
    config: AnalysisConfig = Field(default_factory=AnalysisConfig)


class AnalysisStageUpdate(BaseModel):
    stage: Literal["before", "off", "after"] | None = None


class AnalysisRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    audio_file_id: str
    status: str
    stage: str | None = None
    algorithm_version: str
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AnalysisRunDetail(AnalysisRunSummary):
    config: dict | None = None
    quality: dict | None = None
    result: dict | None = None
