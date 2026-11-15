from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import AnalysisRun, Measurement, MetricPoint, NoiseEvent
from app.schemas.dashboard import DashboardEvent, DashboardKpi, DashboardMeasurement, DashboardRead
from app.schemas.measurement import MeasurementSummary, MetricPointRead
from app.services.maintenance import system_status

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

LATEST_POINT_LIMIT = 3600


def _event_brief(event: NoiseEvent) -> DashboardEvent:
    return DashboardEvent(
        id=event.id,
        title=event.title,
        status=event.status,
        location=event.location,
        measurement_point=event.measurement_point,
        created_at=event.created_at,
        analysis_count=len(event.analyses),
    )


@router.get("", response_model=DashboardRead)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardRead:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    measurement_total = db.scalar(select(func.count()).select_from(Measurement)) or 0
    measurement_24h = (
        db.scalar(
            select(func.count())
            .select_from(Measurement)
            .where(Measurement.started_at >= since)
        )
        or 0
    )
    recorded_seconds = (
        db.scalar(select(func.coalesce(func.sum(Measurement.duration_s), 0.0))) or 0.0
    )
    events_open = (
        db.scalar(
            select(func.count()).select_from(NoiseEvent).where(NoiseEvent.status == "open")
        )
        or 0
    )
    events_total = db.scalar(select(func.count()).select_from(NoiseEvent)) or 0
    analyses_total = db.scalar(select(func.count()).select_from(AnalysisRun)) or 0
    analyses_pending = (
        db.scalar(
            select(func.count())
            .select_from(AnalysisRun)
            .where(AnalysisRun.status.in_(("queued", "running")))
        )
        or 0
    )
    analyses_failed = (
        db.scalar(
            select(func.count()).select_from(AnalysisRun).where(AnalysisRun.status == "failed")
        )
        or 0
    )

    system = system_status()

    recent_measurements = list(
        db.scalars(
            select(Measurement).order_by(Measurement.started_at.desc()).limit(6)
        )
    )
    latest = recent_measurements[0] if recent_measurements else None

    latest_measurement: DashboardMeasurement | None = None
    if latest is not None:
        points = list(
            db.scalars(
                select(MetricPoint)
                .where(MetricPoint.measurement_id == latest.id)
                .order_by(MetricPoint.t_offset_s)
                .limit(LATEST_POINT_LIMIT)
            )
        )
        latest_measurement = DashboardMeasurement.model_validate(latest)
        latest_measurement.points = [MetricPointRead.model_validate(point) for point in points]

    pending_events = list(
        db.scalars(
            select(NoiseEvent)
            .where(NoiseEvent.status == "open")
            .order_by(NoiseEvent.created_at.desc())
            .limit(5)
        )
    )
    recent_events = list(
        db.scalars(select(NoiseEvent).order_by(NoiseEvent.created_at.desc()).limit(5))
    )

    return DashboardRead(
        generated_at=now,
        kpi=DashboardKpi(
            measurements=measurement_total,
            measurements_24h=measurement_24h,
            recorded_seconds=round(float(recorded_seconds), 1),
            events_open=events_open,
            events_total=events_total,
            analyses_total=analyses_total,
            analyses_pending=analyses_pending,
            analyses_failed=analyses_failed,
            audio_bytes=int((system.get("audio") or {}).get("bytes") or 0),
        ),
        latest_measurement=latest_measurement,
        recent_measurements=[MeasurementSummary.model_validate(item) for item in recent_measurements],
        pending_events=[_event_brief(event) for event in pending_events],
        recent_events=[_event_brief(event) for event in recent_events],
        system=system,
    )
