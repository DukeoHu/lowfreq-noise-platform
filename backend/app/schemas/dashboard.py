from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.measurement import MeasurementSummary, MetricPointRead


class DashboardKpi(BaseModel):
    measurements: int
    measurements_24h: int
    recorded_seconds: float
    events_open: int
    events_total: int
    analyses_total: int
    analyses_pending: int
    analyses_failed: int
    audio_bytes: int


class DashboardEvent(BaseModel):
    id: str
    title: str
    status: str
    location: str
    measurement_point: str
    created_at: datetime
    analysis_count: int


class DashboardMeasurement(MeasurementSummary):
    points: list[MetricPointRead] = Field(default_factory=list)


class DashboardRead(BaseModel):
    generated_at: datetime
    kpi: DashboardKpi
    latest_measurement: DashboardMeasurement | None = None
    recent_measurements: list[MeasurementSummary] = Field(default_factory=list)
    pending_events: list[DashboardEvent] = Field(default_factory=list)
    recent_events: list[DashboardEvent] = Field(default_factory=list)
    system: dict
