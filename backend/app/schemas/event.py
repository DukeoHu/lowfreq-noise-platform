from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    location: str = ""
    measurement_point: str = ""
    equipment: str = ""
    notes: str = ""
    tags: list[str] = []
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    status: Literal["open", "investigating", "resolved"] = "open"


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    location: str | None = None
    measurement_point: str | None = None
    equipment: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    status: Literal["open", "investigating", "resolved"] | None = None


class EventPhotoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    size_bytes: int
    created_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    location: str
    measurement_point: str = ""
    equipment: str = ""
    notes: str = ""
    tags: list[str] = []
    latitude: float | None = None
    longitude: float | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    analysis_run_ids: list[str] = []
    photos: list[EventPhotoRead] = []


class EventAnalysisSummary(BaseModel):
    """事件下单个关联分析的关键指标，用于整改前后（OFF/ON）对比。"""

    run_id: str
    audio_filename: str
    status: str
    stage: str | None = None
    created_at: datetime
    duration_s: float | None = None
    laeq: float | None = None
    lceq: float | None = None
    lzeq: float | None = None
    c_minus_a: float | None = None
    dominant_peak_hz: float | None = None
    dominant_peak_level: float | None = None
    calibrated: bool | None = None
    flags: list[str] = []


class EventSummary(BaseModel):
    event: EventRead
    analyses: list[EventAnalysisSummary]
    band_comparison: dict | None = Field(
        default=None, description="服务端计算的阶段 1/3 倍频程差值与 ≥10 dB 判定"
    )
