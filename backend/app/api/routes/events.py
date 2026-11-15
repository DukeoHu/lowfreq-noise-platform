from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import AnalysisRun, EventInsight, EventPhoto, NoiseEvent
from app.schemas.event import (
    EventAnalysisSummary,
    EventCreate,
    EventPhotoRead,
    EventRead,
    EventSummary,
    EventUpdate,
)
from app.schemas.insight import InsightFeedback, InsightRead, InsightVersionRead
from app.services import audit, insight as insight_service, storage
from app.workers.runner import load_result

router = APIRouter(prefix="/events", tags=["events"])

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
PHOTO_MAX_BYTES = 10 * 1024 * 1024
STAGE_PATTERN = "^(before|off|after)$"


def _to_read(event: NoiseEvent) -> EventRead:
    data = EventRead.model_validate(event)
    data.analysis_run_ids = [run.id for run in event.analyses]
    return data


@router.post("", response_model=EventRead, status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreate, db: Session = Depends(get_db)) -> EventRead:
    event = NoiseEvent(**payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    audit.record("event.create", entity_type="event", entity_id=event.id, detail={"title": event.title})
    return _to_read(event)


@router.get("", response_model=list[EventRead])
def list_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[EventRead]:
    stmt = select(NoiseEvent).order_by(NoiseEvent.created_at.desc()).limit(limit).offset(offset)
    return [_to_read(event) for event in db.scalars(stmt)]


@router.get("/{event_id}", response_model=EventRead)
def get_event(event_id: str, db: Session = Depends(get_db)) -> EventRead:
    event = db.get(NoiseEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件不存在")
    return _to_read(event)


@router.patch("/{event_id}", response_model=EventRead)
def update_event(event_id: str, payload: EventUpdate, db: Session = Depends(get_db)) -> EventRead:
    event = db.get(NoiseEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件不存在")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(event, field, value)
    db.commit()
    db.refresh(event)
    audit.record(
        "event.update",
        entity_type="event",
        entity_id=event.id,
        detail={"fields": sorted(changes)},
    )
    return _to_read(event)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: str, db: Session = Depends(get_db)) -> None:
    event = db.get(NoiseEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件不存在")

    settings = get_settings()
    photo_paths = [storage.resolve(photo.stored_path, settings.resolved_data_dir) for photo in event.photos]
    detail = {
        "title": event.title,
        "photo_count": len(photo_paths),
        "analysis_ids": [run.id for run in event.analyses],
    }
    event.analyses.clear()
    db.delete(event)
    db.commit()
    for path in photo_paths:
        path.unlink(missing_ok=True)
    audit.record("event.delete", entity_type="event", entity_id=event_id, detail=detail)


@router.post("/{event_id}/analyses/{run_id}", response_model=EventRead)
def link_analysis(
    event_id: str,
    run_id: str,
    stage: str | None = Query(default=None, pattern=STAGE_PATTERN),
    db: Session = Depends(get_db),
) -> EventRead:
    event = db.get(NoiseEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件不存在")
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    if stage is not None:
        run.stage = stage
        db.commit()
    if run not in event.analyses:
        event.analyses.append(run)
        db.commit()
        db.refresh(event)
    audit.record(
        "event.link_analysis",
        entity_type="event",
        entity_id=event.id,
        detail={"run_id": run_id, "stage": stage},
    )
    return _to_read(event)


@router.delete("/{event_id}/analyses/{run_id}", response_model=EventRead)
def unlink_analysis(event_id: str, run_id: str, db: Session = Depends(get_db)) -> EventRead:
    event = db.get(NoiseEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件不存在")
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    if run not in event.analyses:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该分析未关联此事件")
    event.analyses.remove(run)
    db.commit()
    db.refresh(event)
    audit.record(
        "event.unlink_analysis",
        entity_type="event",
        entity_id=event.id,
        detail={"run_id": run_id},
    )
    return _to_read(event)


@router.post(
    "/{event_id}/photos",
    response_model=EventPhotoRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_event_photo(
    event_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> EventPhoto:
    event = db.get(NoiseEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件不存在")

    settings = get_settings()
    filename = file.filename or "photo"
    suffix = Path(filename).suffix.lower()
    if suffix not in PHOTO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的图片类型 {suffix or '(无扩展名)'}，允许: {', '.join(sorted(PHOTO_EXTENSIONS))}",
        )
    try:
        stored = storage.save_stream(
            file.file,
            original_filename=filename,
            target_dir=settings.photos_dir,
            data_dir=settings.resolved_data_dir,
            max_bytes=PHOTO_MAX_BYTES,
        )
    except storage.UploadTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc

    photo = EventPhoto(
        event_id=event.id,
        original_filename=filename,
        stored_path=stored.relative_path,
        size_bytes=stored.size_bytes,
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)
    audit.record(
        "event.photo_upload",
        entity_type="event",
        entity_id=event.id,
        detail={"photo_id": photo.id, "filename": filename},
    )
    return photo


@router.get("/{event_id}/photos/{photo_id}")
def download_event_photo(
    event_id: str,
    photo_id: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    photo = db.get(EventPhoto, photo_id)
    if photo is None or photo.event_id != event_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="照片不存在")
    settings = get_settings()
    path = storage.resolve(photo.stored_path, settings.resolved_data_dir)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="照片文件已丢失")
    media_type = mimetypes.guess_type(photo.original_filename)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=photo.original_filename)


@router.delete("/{event_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event_photo(event_id: str, photo_id: str, db: Session = Depends(get_db)) -> None:
    photo = db.get(EventPhoto, photo_id)
    if photo is None or photo.event_id != event_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="照片不存在")
    settings = get_settings()
    storage.resolve(photo.stored_path, settings.resolved_data_dir).unlink(missing_ok=True)
    db.delete(photo)
    db.commit()
    audit.record(
        "event.photo_delete",
        entity_type="event",
        entity_id=event_id,
        detail={"photo_id": photo_id},
    )


@router.get("/{event_id}/summary", response_model=EventSummary)
def get_event_summary(event_id: str, db: Session = Depends(get_db)) -> EventSummary:
    event = db.get(NoiseEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件不存在")

    analyses: list[EventAnalysisSummary] = []
    for run in sorted(event.analyses, key=lambda item: item.created_at):
        item = EventAnalysisSummary(
            run_id=run.id,
            audio_filename=run.audio_file.original_filename,
            status=run.status,
            stage=run.stage,
            created_at=run.created_at,
        )
        result = load_result(run)
        if result:
            overall = result.get("overall") or {}
            item.duration_s = (result.get("audio") or {}).get("duration_s")
            item.laeq = overall.get("laeq")
            item.lceq = overall.get("lceq")
            item.lzeq = overall.get("lzeq")
            item.c_minus_a = overall.get("c_minus_a")
            peaks = result.get("peaks") or []
            if peaks:
                item.dominant_peak_hz = peaks[0].get("fc")
                item.dominant_peak_level = peaks[0].get("level")
            quality = result.get("quality") or {}
            item.calibrated = quality.get("calibrated")
            item.flags = list(quality.get("flags") or [])
        analyses.append(item)

    return EventSummary(
        event=_to_read(event),
        analyses=analyses,
        band_comparison=insight_service.event_band_comparison(event),
    )


@router.get("/{event_id}/insight", response_model=InsightRead | None)
def get_event_insight(event_id: str, db: Session = Depends(get_db)) -> EventInsight | None:
    event = db.get(NoiseEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件不存在")
    return db.scalars(select(EventInsight).where(EventInsight.event_id == event_id)).first()


@router.post("/{event_id}/insight", response_model=InsightRead, status_code=status.HTTP_202_ACCEPTED)
def generate_event_insight(
    event_id: str,
    force: bool = Query(default=False, description="忽略缓存并重新调用模型"),
    db: Session = Depends(get_db),
) -> EventInsight:
    """基于事件关联分析的 before/off/after 指标生成对比解读。

    阶段频段差值由服务端计算；命中缓存或任务进行中时不会重复调用模型。
    """
    event = db.get(NoiseEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件不存在")
    if not any(run.status == "succeeded" for run in event.analyses):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="事件还没有成功的分析结果，无法生成解读",
        )
    if not insight_service.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="未配置大模型，请设置 LOWFREQ_LLM_* 环境变量",
        )

    try:
        record = insight_service.queue_event_insight(event_id, force=force)
    except insight_service.InsightBudgetExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except insight_service.InsightQueueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    audit.record(
        "event.insight",
        entity_type="event",
        entity_id=event_id,
        detail={
            "model": record.model,
            "prompt_version": record.prompt_version,
            "force": force,
            "cache_hit": record.cache_hit,
        },
    )
    return record


@router.post("/{event_id}/insight/cancel", response_model=InsightRead)
def cancel_event_insight(event_id: str) -> EventInsight:
    """取消排队中或生成中的对比解读。"""
    try:
        record = insight_service.cancel_event_insight(event_id)
    except insight_service.InsightQueueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit.record("event.insight_cancel", entity_type="event", entity_id=event_id, detail={})
    return record


@router.get("/{event_id}/insight/history", response_model=list[InsightVersionRead])
def list_event_insight_history(
    event_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> list:
    return insight_service.event_insight_history(event_id, limit=limit)


@router.post("/{event_id}/insight/feedback", response_model=InsightRead)
def submit_event_insight_feedback(
    event_id: str,
    payload: InsightFeedback,
) -> EventInsight:
    """记录人工反馈：有用 / 无用 / 已采纳。"""
    try:
        record = insight_service.save_event_feedback(event_id, payload.rating, payload.note)
    except insight_service.InsightQueueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit.record(
        "event.insight_feedback",
        entity_type="event",
        entity_id=event_id,
        detail={"rating": payload.rating},
    )
    return record
