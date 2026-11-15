"""任务执行器（单机线程池 + 启动恢复）。

MVP 阶段不引入 Celery：使用小规模线程池执行分析任务，进程重启时把仍处于
queued/running 的任务重新入队，避免进程崩溃后任务永久卡住。后续切换 Celery 时
只需替换 submit_run 的实现，调用侧无需改动。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import select

from app.core.config import get_settings
from app.db.base import get_session_factory
from app.db.models import AnalysisRun
from app.schemas.analysis import AnalysisConfig
from app.services import storage
from app.services.llm_config import load_runtime_config
from app.services.pipeline import run_analysis

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_executor_lock = Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                workers = max(1, get_settings().analysis_workers)
                _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analysis")
    return _executor


def submit_run(run_id: str) -> None:
    """把分析任务提交到线程池，不阻塞请求线程。"""
    _get_executor().submit(execute_run, run_id)


def recover_pending_runs() -> int:
    """启动时恢复未完成任务：running 视为上次进程中断，重置为 queued 后重新入队。"""
    session = get_session_factory()()
    try:
        runs = list(
            session.scalars(select(AnalysisRun).where(AnalysisRun.status.in_(("queued", "running")))).all()
        )
        for run in runs:
            if run.status == "running":
                run.status = "queued"
                run.started_at = None
                run.error = None
        if runs:
            session.commit()
        for run in runs:
            submit_run(run.id)
        if runs:
            logger.info("recovered %d pending analysis run(s)", len(runs))
        return len(runs)
    except Exception:
        session.rollback()
        logger.exception("failed to recover pending analysis runs")
        return 0
    finally:
        session.close()


def shutdown_runner() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=False)
        _executor = None


def execute_run(run_id: str) -> None:
    session = get_session_factory()()
    try:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            logger.warning("analysis run %s not found", run_id)
            return
        if run.status not in {"queued", "running"}:
            return

        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        session.commit()

        settings = get_settings()
        audio_path = storage.resolve(run.audio_file.stored_path, settings.resolved_data_dir)
        config = AnalysisConfig.model_validate(run.config)
        result = run_analysis(audio_path, config)

        # 计算期间任务可能已被取消，落盘前重新确认状态，避免取消后仍写入结果
        session.refresh(run)
        if run.status != "running":
            logger.info("analysis run %s is %s; discard computed result", run_id, run.status)
            return

        relative_path = f"results/{run.id}.json"
        storage.write_json(settings.resolved_data_dir / relative_path, result)

        run.result_path = relative_path
        run.quality = result["quality"]
        run.status = "succeeded"
        run.finished_at = datetime.now(timezone.utc)
        session.commit()

        # 可选自动生成 AI 解读：只入队，不阻塞分析线程
        llm_runtime = load_runtime_config()
        if llm_runtime.auto_insight and llm_runtime.configured:
            from app.services import insight as insight_service

            insight_service.submit_analysis_insight(run.id)
    except Exception as exc:  # 分析失败必须显式落库，前端据此禁止出正式报告
        session.rollback()
        logger.exception("analysis run %s failed", run_id)
        failed = session.get(AnalysisRun, run_id)
        if failed is not None and failed.status == "running":
            failed.status = "failed"
            failed.error = f"{type(exc).__name__}: {exc}"
            failed.finished_at = datetime.now(timezone.utc)
            session.commit()
    finally:
        session.close()


def load_result(run: AnalysisRun) -> dict | None:
    if not run.result_path:
        return None
    settings = get_settings()
    path = settings.resolved_data_dir / run.result_path
    if not path.exists():
        return None
    return storage.read_json(path)
