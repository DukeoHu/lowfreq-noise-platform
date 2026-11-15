from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import current_actor
from app.db.base import get_db
from app.db.models import LlmConfig
from app.schemas.llm import LlmConfigRead, LlmConfigUpdate, LlmTestResult, LlmUsageRead
from app.services import audit, insight as insight_service, llm, llm_config
from app.services.maintenance import system_status

router = APIRouter(prefix="/system", tags=["system"])


def _read_view(db: Session) -> LlmConfigRead:
    runtime = llm_config.load_runtime_config(db)
    row = db.get(LlmConfig, llm_config.CONFIG_ID)
    return LlmConfigRead(
        enabled=runtime.enabled,
        base_url=runtime.base_url,
        model=runtime.model,
        timeout_s=runtime.timeout_s,
        max_tokens=runtime.max_tokens,
        auto_insight=runtime.auto_insight,
        redact_context=runtime.redact_context,
        api_key_env_only=runtime.api_key_env_only,
        monthly_token_budget=runtime.monthly_token_budget,
        has_api_key=bool(runtime.api_key),
        source=runtime.source,
        updated_at=row.updated_at if row else None,
        updated_by=row.updated_by if row else None,
    )


@router.get("/status")
def read_system_status() -> dict:
    return system_status()


@router.get("/llm", response_model=LlmConfigRead)
def get_llm_config(db: Session = Depends(get_db)) -> LlmConfigRead:
    """返回当前生效的 LLM 配置；密钥只显示是否已配置。"""
    return _read_view(db)


@router.put("/llm", response_model=LlmConfigRead)
def update_llm_config(payload: LlmConfigUpdate, db: Session = Depends(get_db)) -> LlmConfigRead:
    base_url = payload.base_url.strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Base URL 需以 http:// 或 https:// 开头")
    if payload.enabled and not (base_url and payload.model.strip()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="启用时必须填写 Base URL 与模型名",
        )

    try:
        llm_config.save_runtime_config(payload, actor=current_actor())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    view = _read_view(db)
    audit.record(
        "system.llm_update",
        entity_type="system",
        entity_id="llm",
        detail={
            "enabled": view.enabled,
            "base_url": view.base_url,
            "model": view.model,
            "has_api_key": view.has_api_key,
            "auto_insight": view.auto_insight,
        },
    )
    return view


@router.get("/llm/usage", response_model=LlmUsageRead)
def get_llm_usage() -> LlmUsageRead:
    """本月 AI 解读 token 用量与预算使用情况。"""
    return LlmUsageRead(**insight_service.monthly_usage())


@router.post("/llm/test", response_model=LlmTestResult)
def test_llm_config() -> LlmTestResult:
    """用最小请求验证当前保存的配置是否可用。"""
    if not llm.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="未配置大模型，请先保存启用状态、Base URL、模型与 API Key",
        )
    try:
        reply = llm.test_connection()
    except llm.LLMError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return LlmTestResult(ok=True, model=llm.get_config().model, reply=reply)
