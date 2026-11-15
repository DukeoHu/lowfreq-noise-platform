from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Measurement, MeasurementSegment, MetricPoint
from app.schemas.measurement import (
    MeasurementDetail,
    MeasurementSegmentRead,
    MeasurementSummary,
    MetricPointRead,
)

router = APIRouter(prefix="/measurements", tags=["measurements"])


@router.get("", response_model=list[MeasurementSummary])
def list_measurements(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Measurement]:
    stmt = (
        select(Measurement)
        .order_by(Measurement.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt))


@router.get("/{measurement_id}", response_model=MeasurementDetail)
def get_measurement(
    measurement_id: str,
    point_limit: int = Query(default=7200, ge=1, le=86400),
    point_offset: int = Query(default=0, ge=0, description="按 t_offset_s 升序跳过前 N 个点"),
    db: Session = Depends(get_db),
) -> MeasurementDetail:
    measurement = db.get(Measurement, measurement_id)
    if measurement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="测量记录不存在")

    points = list(
        db.scalars(
            select(MetricPoint)
            .where(MetricPoint.measurement_id == measurement_id)
            .order_by(MetricPoint.t_offset_s)
            .offset(point_offset)
            .limit(point_limit)
        )
    )
    segments = list(
        db.scalars(
            select(MeasurementSegment)
            .where(MeasurementSegment.measurement_id == measurement_id)
            .order_by(MeasurementSegment.segment_index)
        )
    )

    detail = MeasurementDetail.model_validate(measurement)
    detail.points = [MetricPointRead.model_validate(point) for point in points]
    detail.segments = [MeasurementSegmentRead.model_validate(segment) for segment in segments]
    return detail
