from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import (
    analyses,
    audio_files,
    audit,
    dashboard,
    events,
    health,
    measurements,
    realtime,
    system,
)
from app.core.config import get_settings
from app.core.security import AccessTokenMiddleware
from app.db.base import init_db
from app.services.maintenance import sweep_expired_audio
from app.services.measurement import close_stale_measurements
from app.services.insight import recover_stale_insights, shutdown_insight_runner
from app.workers.runner import recover_pending_runs, shutdown_runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)


async def _maintenance_loop(interval_hours: int) -> None:
    while True:
        await asyncio.sleep(max(1, interval_hours) * 3600)
        try:
            await asyncio.to_thread(sweep_expired_audio)
        except Exception:
            logger.exception("periodic retention sweep failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    init_db()
    recover_pending_runs()
    recover_stale_insights()
    close_stale_measurements()
    await asyncio.to_thread(sweep_expired_audio)
    maintenance = asyncio.create_task(_maintenance_loop(settings.retention_sweep_hours))
    try:
        yield
    finally:
        maintenance.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance
        shutdown_runner()
        shutdown_insight_runner()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="低频噪音 Web 平台 - MVP（实时测量 + 上传音频 + 离线分析）",
        lifespan=lifespan,
    )
    # 先加鉴权、后加 CORS：CORS 位于最外层，401 响应也能带上跨域头
    app.add_middleware(AccessTokenMiddleware, token=settings.access_token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(audio_files.router, prefix=settings.api_prefix)
    app.include_router(analyses.router, prefix=settings.api_prefix)
    app.include_router(events.router, prefix=settings.api_prefix)
    app.include_router(realtime.router, prefix=settings.api_prefix)
    app.include_router(measurements.router, prefix=settings.api_prefix)
    app.include_router(dashboard.router, prefix=settings.api_prefix)
    app.include_router(audit.router, prefix=settings.api_prefix)
    app.include_router(system.router, prefix=settings.api_prefix)

    @app.get("/")
    def root() -> dict:
        return {"name": settings.app_name, "version": __version__, "docs": "/docs", "api": settings.api_prefix}

    return app


app = create_app()
