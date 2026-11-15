from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import AnalysisInsight, AnalysisRun, AudioFile
from app.schemas.analysis import AnalysisCreate, AnalysisRunDetail, AnalysisRunSummary, AnalysisStageUpdate
from app.schemas.common import Page
from app.schemas.insight import InsightFeedback, InsightRead, InsightVersionRead
from app.services import audio_io, audit, insight as insight_service, storage
from app.services.pipeline import ALGORITHM_VERSION
from app.services.report import build_report_pdf
from app.workers.runner import load_result, submit_run

router = APIRouter(prefix="/analyses", tags=["analyses"])


def _bands_csv_text(result: dict) -> str:
    z_bands = result["bands"]["z_weighted"]
    a_by_fc = {item["fc"]: item["level"] for item in result["bands"]["a_weighted"]}
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["center_frequency_hz", "z_level_db", "a_level_db"])
    for band in z_bands:
        writer.writerow([band["fc"], band["level"], a_by_fc.get(band["fc"])])
    return buffer.getvalue()


def _report_pdf(run: AnalysisRun, result: dict, insight: dict | None = None) -> bytes:
    return build_report_pdf(
        run_id=run.id,
        audio_filename=run.audio_file.original_filename,
        created_at=run.created_at,
        algorithm_version=run.algorithm_version,
        result=result,
        config=run.config,
        insight=insight,
    )


def _insight_content(db: Session, run_id: str) -> dict | None:
    record = db.scalars(select(AnalysisInsight).where(AnalysisInsight.run_id == run_id)).first()
    if record is None or record.status != "succeeded" or not record.content:
        return None
    return record.content


@router.post("", response_model=AnalysisRunSummary, status_code=status.HTTP_202_ACCEPTED)
def create_analysis(
    payload: AnalysisCreate,
    db: Session = Depends(get_db),
) -> AnalysisRun:
    audio = db.get(AudioFile, payload.audio_file_id)
    if audio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="音频文件不存在")

    try:
        audio_io.ensure_duration_allowed(audio.duration_s, get_settings().analysis_max_seconds)
    except audio_io.AudioTooLongError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    run = AnalysisRun(
        audio_file_id=audio.id,
        status="queued",
        algorithm_version=ALGORITHM_VERSION,
        config=payload.config.model_dump(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    audit.record(
        "analysis.create",
        entity_type="analysis",
        entity_id=run.id,
        detail={
            "audio_file_id": audio.id,
            "audio_filename": audio.original_filename,
            "calibration": payload.config.calibration.model_dump(),
            "capture": payload.config.capture.model_dump() if payload.config.capture else None,
        },
    )

    submit_run(run.id)
    return run


@router.get("", response_model=Page[AnalysisRunSummary])
def list_analyses(
    audio_file_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status", description="按状态过滤"),
    filename: str | None = Query(default=None, description="按音频文件名模糊搜索"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page:
    stmt = select(AnalysisRun)
    count_stmt = select(func.count()).select_from(AnalysisRun)
    if filename:
        pattern = f"%{filename.strip()}%"
        join_condition = AnalysisRun.audio_file_id == AudioFile.id
        stmt = stmt.join(AudioFile, join_condition)
        count_stmt = count_stmt.join(AudioFile, join_condition)
        stmt = stmt.where(AudioFile.original_filename.ilike(pattern))
        count_stmt = count_stmt.where(AudioFile.original_filename.ilike(pattern))
    if audio_file_id:
        stmt = stmt.where(AnalysisRun.audio_file_id == audio_file_id)
        count_stmt = count_stmt.where(AnalysisRun.audio_file_id == audio_file_id)
    if status_filter:
        stmt = stmt.where(AnalysisRun.status == status_filter)
        count_stmt = count_stmt.where(AnalysisRun.status == status_filter)

    total = int(db.scalar(count_stmt) or 0)
    items = list(db.scalars(stmt.order_by(AnalysisRun.created_at.desc()).limit(limit).offset(offset)))
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{run_id}", response_model=AnalysisRunDetail)
def get_analysis(run_id: str, db: Session = Depends(get_db)) -> AnalysisRunDetail:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    detail = AnalysisRunDetail.model_validate(run)
    detail.result = load_result(run)
    return detail


@router.patch("/{run_id}", response_model=AnalysisRunSummary)
def update_analysis_stage(
    run_id: str,
    payload: AnalysisStageUpdate,
    db: Session = Depends(get_db),
) -> AnalysisRun:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    run.stage = payload.stage
    db.commit()
    db.refresh(run)
    audit.record(
        "analysis.stage",
        entity_type="analysis",
        entity_id=run.id,
        detail={"stage": payload.stage},
    )
    return run


@router.post("/{run_id}/retry", response_model=AnalysisRunSummary, status_code=status.HTTP_202_ACCEPTED)
def retry_analysis(run_id: str, db: Session = Depends(get_db)) -> AnalysisRun:
    """仅允许重试失败任务：清空错误与旧结果后重新入队。"""
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    if run.status != "failed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有失败的任务可以重试")

    run.status = "queued"
    run.error = None
    run.result = None
    run.quality = None
    run.result_path = None
    run.started_at = None
    run.finished_at = None
    db.commit()
    db.refresh(run)
    audit.record("analysis.retry", entity_type="analysis", entity_id=run.id, detail={})
    submit_run(run.id)
    return run


@router.post("/{run_id}/cancel", response_model=AnalysisRunSummary)
def cancel_analysis(run_id: str, db: Session = Depends(get_db)) -> AnalysisRun:
    """取消排队中或计算中的任务；执行器发现状态变化后不会写入结果。"""
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    if run.status not in {"queued", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有排队中或计算中的任务可以取消")

    run.status = "cancelled"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    audit.record("analysis.cancel", entity_type="analysis", entity_id=run.id, detail={})
    return run


@router.get("/{run_id}/insight", response_model=InsightRead | None)
def get_analysis_insight(run_id: str, db: Session = Depends(get_db)) -> AnalysisInsight | None:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    return db.scalars(select(AnalysisInsight).where(AnalysisInsight.run_id == run_id)).first()


@router.post("/{run_id}/insight", response_model=InsightRead, status_code=status.HTTP_202_ACCEPTED)
def generate_analysis_insight(
    run_id: str,
    force: bool = Query(default=False, description="忽略缓存并重新调用模型"),
    db: Session = Depends(get_db),
) -> AnalysisInsight:
    """手动生成/重新生成 AI 解读；仅对已完成的分析开放。

    同一结果、模型与提示词版本命中缓存时直接返回已有内容，不再调用模型；
    排队中或运行中的请求会被复用，避免重复消耗 token。
    """
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    if run.status != "succeeded":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分析尚未成功完成，无法生成解读")
    if not insight_service.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="未配置大模型，请设置 LOWFREQ_LLM_* 环境变量",
        )

    try:
        record = insight_service.queue_analysis_insight(run_id, force=force)
    except insight_service.InsightBudgetExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except insight_service.InsightQueueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    audit.record(
        "analysis.insight",
        entity_type="analysis",
        entity_id=run_id,
        detail={
            "model": record.model,
            "prompt_version": record.prompt_version,
            "force": force,
            "cache_hit": record.cache_hit,
        },
    )
    return record


@router.post("/{run_id}/insight/cancel", response_model=InsightRead)
def cancel_analysis_insight(run_id: str) -> AnalysisInsight:
    """取消排队中或生成中的解读；模型返回后不会覆盖取消状态。"""
    try:
        record = insight_service.cancel_analysis_insight(run_id)
    except insight_service.InsightQueueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit.record("analysis.insight_cancel", entity_type="analysis", entity_id=run_id, detail={})
    return record


@router.get("/{run_id}/insight/history", response_model=list[InsightVersionRead])
def list_analysis_insight_history(
    run_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> list:
    """历史版本按生成时间倒序返回，用于回溯与人工对比。"""
    return insight_service.analysis_insight_history(run_id, limit=limit)


@router.post("/{run_id}/insight/feedback", response_model=InsightRead)
def submit_analysis_insight_feedback(
    run_id: str,
    payload: InsightFeedback,
) -> AnalysisInsight:
    """记录人工反馈：有用 / 无用 / 已采纳。"""
    try:
        record = insight_service.save_analysis_feedback(run_id, payload.rating, payload.note)
    except insight_service.InsightQueueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit.record(
        "analysis.insight_feedback",
        entity_type="analysis",
        entity_id=run_id,
        detail={"rating": payload.rating},
    )
    return record


@router.get("/{run_id}/bands.csv")
def download_bands_csv(run_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    result = load_result(run)
    if not result:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分析尚未完成")

    audit.record("analysis.export", entity_type="analysis", entity_id=run.id, detail={"format": "csv"})
    return StreamingResponse(
        iter([_bands_csv_text(result)]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="analysis-{run_id}-bands.csv"'},
    )


@router.get("/{run_id}/result.json")
def download_result_json(run_id: str, db: Session = Depends(get_db)) -> Response:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    result = load_result(run)
    if not result:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分析尚未完成，无法导出结果")

    payload = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    audit.record("analysis.export", entity_type="analysis", entity_id=run.id, detail={"format": "json"})
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="analysis-{run_id}.json"'},
    )


@router.get("/{run_id}/export.zip")
def download_export_zip(
    run_id: str,
    include_audio: bool = Query(default=False, description="是否把原始音频一并打包"),
    db: Session = Depends(get_db),
) -> Response:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    result = load_result(run)
    if not result:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分析尚未完成，无法导出数据包")

    settings = get_settings()
    folder = f"analysis-{run_id}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(f"{folder}/result.json", json.dumps(result, ensure_ascii=False, indent=2))
        bundle.writestr(f"{folder}/bands-third-octave.csv", _bands_csv_text(result))
        bundle.writestr(f"{folder}/report.pdf", _report_pdf(run, result, _insight_content(db, run_id)))
        if include_audio:
            audio_path = storage.resolve(run.audio_file.stored_path, settings.resolved_data_dir)
            if audio_path.exists():
                bundle.write(audio_path, arcname=f"{folder}/audio/{run.audio_file.original_filename}")

    audit.record(
        "analysis.export",
        entity_type="analysis",
        entity_id=run.id,
        detail={"format": "zip", "include_audio": include_audio},
    )
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{folder}.zip"'},
    )


@router.get("/{run_id}/report.pdf")
def download_report_pdf(run_id: str, db: Session = Depends(get_db)) -> Response:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")
    result = load_result(run)
    if not result:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分析尚未完成，无法生成报告")

    audit.record("analysis.export", entity_type="analysis", entity_id=run.id, detail={"format": "pdf"})
    return Response(
        content=_report_pdf(run, result, _insight_content(db, run_id)),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="lowfreq-report-{run_id}.pdf"'},
    )
