from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import AudioFile, MeasurementSegment
from app.schemas.audio import AudioFileRead
from app.schemas.common import Page
from app.services import audio_io, audit, storage

router = APIRouter(prefix="/audio-files", tags=["audio"])


@router.post("", response_model=AudioFileRead, status_code=status.HTTP_201_CREATED)
def upload_audio(file: UploadFile = File(...), db: Session = Depends(get_db)) -> AudioFile:
    settings = get_settings()
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in settings.allowed_extension_set:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的文件类型 {suffix or '(无扩展名)'}，允许: {', '.join(sorted(settings.allowed_extension_set))}",
        )

    try:
        stored = storage.save_stream(
            file.file,
            original_filename=filename,
            target_dir=settings.audio_dir,
            data_dir=settings.resolved_data_dir,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        )
    except storage.UploadTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc

    try:
        info = audio_io.probe_audio(stored.absolute_path)
    except audio_io.AudioDecodeError as exc:
        stored.absolute_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        audio_io.ensure_duration_allowed(float(info["duration_s"] or 0.0), settings.analysis_max_seconds)
    except audio_io.AudioTooLongError as exc:
        stored.absolute_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc

    record = AudioFile(
        original_filename=filename,
        stored_path=stored.relative_path,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        sample_rate=info["sample_rate"],
        channels=info["channels"],
        duration_s=info["duration_s"],
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    audit.record(
        "audio.upload",
        entity_type="audio",
        entity_id=record.id,
        detail={
            "filename": filename,
            "size_bytes": record.size_bytes,
            "sample_rate": record.sample_rate,
            "duration_s": record.duration_s,
            "sha256": record.sha256,
        },
    )
    return record


@router.get("", response_model=Page[AudioFileRead])
def list_audio_files(
    filename: str | None = Query(default=None, description="按原始文件名模糊搜索"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page:
    stmt = select(AudioFile)
    count_stmt = select(func.count()).select_from(AudioFile)
    if filename:
        pattern = f"%{filename.strip()}%"
        stmt = stmt.where(AudioFile.original_filename.ilike(pattern))
        count_stmt = count_stmt.where(AudioFile.original_filename.ilike(pattern))
    total = int(db.scalar(count_stmt) or 0)
    items = list(db.scalars(stmt.order_by(AudioFile.created_at.desc()).limit(limit).offset(offset)))
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{audio_file_id}", response_model=AudioFileRead)
def get_audio_file(audio_file_id: str, db: Session = Depends(get_db)) -> AudioFile:
    record = db.get(AudioFile, audio_file_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="音频文件不存在")
    return record


@router.get("/{audio_file_id}/download")
def download_audio_file(audio_file_id: str, db: Session = Depends(get_db)) -> FileResponse:
    record = db.get(AudioFile, audio_file_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="音频文件不存在")
    settings = get_settings()
    path = storage.resolve(record.stored_path, settings.resolved_data_dir)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="音频文件已丢失")
    return FileResponse(path, media_type="application/octet-stream", filename=record.original_filename)


@router.delete("/{audio_file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_audio_file(audio_file_id: str, db: Session = Depends(get_db)) -> None:
    record = db.get(AudioFile, audio_file_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="音频文件不存在")

    settings = get_settings()
    filename = record.original_filename
    runs = list(record.analyses)
    run_ids = [run.id for run in runs]
    result_paths = [
        storage.resolve(run.result_path, settings.resolved_data_dir)
        for run in runs
        if run.result_path
    ]

    # 测量分段与音频/分析之间没有外键，删除前主动断开引用，避免留下悬空 ID
    criteria = [MeasurementSegment.audio_file_id == audio_file_id]
    if run_ids:
        criteria.append(MeasurementSegment.run_id.in_(run_ids))
    segments = list(db.scalars(select(MeasurementSegment).where(or_(*criteria))))
    run_id_set = set(run_ids)
    for segment in segments:
        segment.audio_file_id = None
        if segment.run_id in run_id_set:
            segment.run_id = None
    db.flush()

    storage.resolve(record.stored_path, settings.resolved_data_dir).unlink(missing_ok=True)
    db.delete(record)
    db.commit()

    for path in result_paths:
        path.unlink(missing_ok=True)

    audit.record(
        "audio.delete",
        entity_type="audio",
        entity_id=audio_file_id,
        detail={
            "filename": filename,
            "analysis_count": len(runs),
            "result_files": len(result_paths),
            "segment_count": len(segments),
        },
    )
