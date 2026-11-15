from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MetricPointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    t_offset_s: int
    laeq: float | None = None
    lceq: float | None = None
    lzeq: float | None = None
    laf: float | None = None
    lcf: float | None = None
    lafmax: float | None = None
    lcfmax: float | None = None
    lcpeak: float | None = None
    c_minus_a: float | None = None


class MeasurementSegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    segment_index: int
    run_id: str | None = None
    audio_file_id: str | None = None
    duration_s: float
    interrupted: bool
    created_at: datetime


class MeasurementSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    device_label: str
    sample_rate: int
    offset_db: float
    full_scale_pa: float
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_s: float
    point_count: int
    capture: dict = Field(default_factory=dict)


class MeasurementDetail(MeasurementSummary):
    points: list[MetricPointRead] = Field(default_factory=list)
    segments: list[MeasurementSegmentRead] = Field(default_factory=list)
