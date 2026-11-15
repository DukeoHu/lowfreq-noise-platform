from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LlmConfigUpdate(BaseModel):
    enabled: bool = False
    base_url: str = Field(default="", max_length=255)
    model: str = Field(default="", max_length=120)
    api_key: str | None = Field(default=None, max_length=512, description="留空表示保持现有密钥")
    clear_api_key: bool = False
    timeout_s: float = Field(default=60.0, ge=1, le=600)
    max_tokens: int = Field(default=1500, ge=64, le=8192)
    auto_insight: bool = False
    redact_context: bool = Field(default=True, description="默认脱敏：不发送文件名、位置、备注、设备")
    api_key_env_only: bool = Field(
        default=False, description="仅从环境变量读取 API Key，数据库不保存密钥"
    )
    monthly_token_budget: int = Field(default=0, ge=0, description="月度 token 预算，0 表示不限额")


class LlmConfigRead(BaseModel):
    enabled: bool
    base_url: str
    model: str
    timeout_s: float
    max_tokens: int
    auto_insight: bool
    has_api_key: bool
    redact_context: bool = True
    api_key_env_only: bool = False
    monthly_token_budget: int = 0
    source: str = Field(description="db / env / none")
    updated_at: datetime | None = None
    updated_by: str | None = None


class LlmTestResult(BaseModel):
    ok: bool
    model: str
    reply: str = ""


class LlmUsageRead(BaseModel):
    """月度 token 用量，用于设置页展示与预算拦截。"""

    month: str = Field(description="统计月份，如 2026-11")
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    budget: int = Field(default=0, description="0 表示不限额")
    remaining_tokens: int | None = None
    used_percent: float | None = None
    exceeded: bool = False
