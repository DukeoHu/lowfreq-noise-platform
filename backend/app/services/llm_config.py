"""LLM 运行时配置：数据库单例优先，未配置时回退到环境变量。

前端「设置 → AI 辅助解读」保存后写入 llm_config 表；密钥只保存在服务端，
读取接口仅返回 has_api_key 布尔值，避免明文回传浏览器。

密钥落库策略：
- 默认把 API Key 保存在数据库；
- 勾选「仅环境变量」后数据库不再保存密钥，运行时直接读取 LOWFREQ_LLM_API_KEY，
  适合不希望密钥落盘（含数据库备份）的部署方式。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_session_factory
from app.db.models import LlmConfig
from app.schemas.llm import LlmConfigUpdate

CONFIG_ID = 1


@dataclass(frozen=True)
class LlmRuntimeConfig:
    enabled: bool
    base_url: str
    api_key: str
    model: str
    timeout_s: float
    max_tokens: int
    auto_insight: bool
    source: str
    redact_context: bool = True
    api_key_env_only: bool = False
    monthly_token_budget: int = 0

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key and self.model)


def _from_env() -> LlmRuntimeConfig:
    settings = get_settings()
    has_env = any(
        (settings.llm_enabled, settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    )
    return LlmRuntimeConfig(
        enabled=settings.llm_enabled,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
        max_tokens=settings.llm_max_tokens,
        auto_insight=settings.llm_auto_insight,
        redact_context=settings.llm_redact_context,
        api_key_env_only=settings.llm_api_key_env_only,
        monthly_token_budget=settings.llm_monthly_token_budget,
        source="env" if has_env else "none",
    )


def _from_row(row: LlmConfig) -> LlmRuntimeConfig:
    settings = get_settings()
    # 仅环境变量模式：忽略数据库中的旧密钥，避免迁移后继续使用明文副本
    api_key = settings.llm_api_key if row.api_key_env_only else row.api_key
    return LlmRuntimeConfig(
        enabled=row.enabled,
        base_url=row.base_url,
        api_key=api_key,
        model=row.model,
        timeout_s=row.timeout_s,
        max_tokens=row.max_tokens,
        auto_insight=row.auto_insight,
        redact_context=row.redact_context,
        api_key_env_only=row.api_key_env_only,
        monthly_token_budget=row.monthly_token_budget,
        source="db",
    )


def load_runtime_config(session: Session | None = None) -> LlmRuntimeConfig:
    own_session = session is None
    db = session or get_session_factory()()
    try:
        row = db.get(LlmConfig, CONFIG_ID)
        if row is not None:
            return _from_row(row)
        return _from_env()
    finally:
        if own_session:
            db.close()


def save_runtime_config(payload: LlmConfigUpdate, actor: str) -> LlmConfig:
    session = get_session_factory()()
    try:
        row = session.get(LlmConfig, CONFIG_ID)
        if row is None:
            row = LlmConfig(id=CONFIG_ID)
            session.add(row)
        row.enabled = payload.enabled
        row.base_url = payload.base_url.strip()
        row.model = payload.model.strip()
        row.timeout_s = payload.timeout_s
        row.max_tokens = payload.max_tokens
        row.auto_insight = payload.auto_insight
        row.redact_context = payload.redact_context
        row.api_key_env_only = payload.api_key_env_only
        row.monthly_token_budget = payload.monthly_token_budget
        if payload.api_key_env_only:
            # 仅环境变量模式：主动清空数据库中的密钥副本
            row.api_key = ""
        elif payload.clear_api_key:
            row.api_key = ""
        elif payload.api_key:
            row.api_key = payload.api_key.strip()
        row.updated_by = actor

        env_key = get_settings().llm_api_key
        if row.enabled and row.api_key_env_only and not env_key:
            session.rollback()
            raise ValueError("已选择「仅环境变量」模式，但服务端未设置 LOWFREQ_LLM_API_KEY")
        if row.enabled and not row.api_key_env_only and not row.api_key:
            session.rollback()
            raise ValueError("启用时必须填写 API Key")
        session.commit()
        session.refresh(row)
        return row
    finally:
        session.close()
