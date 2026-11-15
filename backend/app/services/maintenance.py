"""存储保留策略与运行状态统计。"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.base import get_session_factory
from app.db.models import AnalysisRun, AudioFile, EventPhoto
from app.services import audit, storage

logger = logging.getLogger(__name__)


def _dir_stats(path: Path) -> dict[str, int]:
    count = 0
    size = 0
    if path.exists():
        for item in path.rglob("*"):
            if item.is_file():
                count += 1
                size += item.stat().st_size
    return {"count": count, "bytes": size}


def sweep_expired_audio(now: datetime | None = None) -> int:
    """按保留期清理未关联事件的音频及其分析，返回删除数量。

    `LOWFREQ_AUDIO_RETENTION_DAYS=0` 表示关闭自动清理。已关联事件的音频视为
    证据保留，不参与自动删除。
    """
    settings = get_settings()
    if settings.audio_retention_days <= 0:
        return 0

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=settings.audio_retention_days)
    session = get_session_factory()()
    removed = 0
    try:
        candidates = list(session.scalars(select(AudioFile).where(AudioFile.created_at < cutoff)).all())
        for audio in candidates:
            runs = list(audio.analyses)
            if any(run.events for run in runs):
                continue
            for run in runs:
                if run.result_path:
                    (settings.resolved_data_dir / run.result_path).unlink(missing_ok=True)
            storage.resolve(audio.stored_path, settings.resolved_data_dir).unlink(missing_ok=True)
            detail = {
                "filename": audio.original_filename,
                "duration_s": audio.duration_s,
                "size_bytes": audio.size_bytes,
                "retention_days": settings.audio_retention_days,
                "analysis_count": len(runs),
            }
            audio_id = audio.id
            session.delete(audio)
            session.commit()
            audit.record("audio.retention", entity_type="audio", entity_id=audio_id, detail=detail)
            removed += 1
        if removed:
            logger.info("retention removed %d audio file(s)", removed)
        return removed
    except Exception:
        session.rollback()
        logger.exception("audio retention sweep failed")
        return removed
    finally:
        session.close()


def system_status() -> dict:
    settings = get_settings()
    session = get_session_factory()()
    try:
        audio_count = session.scalar(select(func.count()).select_from(AudioFile)) or 0
        audio_bytes = session.scalar(select(func.coalesce(func.sum(AudioFile.size_bytes), 0))) or 0
        photo_count = session.scalar(select(func.count()).select_from(EventPhoto)) or 0
        pending = (
            session.scalar(
                select(func.count())
                .select_from(AnalysisRun)
                .where(AnalysisRun.status.in_(("queued", "running")))
            )
            or 0
        )
        failed = (
            session.scalar(
                select(func.count()).select_from(AnalysisRun).where(AnalysisRun.status == "failed")
            )
            or 0
        )
    finally:
        session.close()

    disk = shutil.disk_usage(settings.resolved_data_dir)
    used_percent = round(disk.used / disk.total * 100, 1) if disk.total else 0.0
    return {
        "data_dir": str(settings.resolved_data_dir),
        "database": settings.resolved_database_url.split(":")[0],
        "retention_days": settings.audio_retention_days,
        "analysis_workers": settings.analysis_workers,
        "audio": {"count": audio_count, "bytes": audio_bytes},
        "photos": {"count": photo_count},
        "results": _dir_stats(settings.result_dir),
        "pending_runs": pending,
        "failed_runs": failed,
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "used_percent": used_percent,
            "warn_percent": settings.disk_warn_percent,
            "warning": used_percent >= settings.disk_warn_percent,
        },
    }
