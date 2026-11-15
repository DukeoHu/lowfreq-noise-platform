from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InsightFinding(BaseModel):
    title: str = Field(default="", description="结论或现象标题")
    detail: str = Field(default="", description="支撑该结论的指标依据")
    evidence: str = Field(default="", description="引用的原始指标，例如 LAeq=48.2 dB、63 Hz=55.1 dB")


class InsightContent(BaseModel):
    """LLM 输出统一结构，单次分析与事件对比共用。

    confidence 与 confidence_basis 由服务端按数据质量规则计算后覆盖，
    validation_warnings 为服务端的数值一致性校验结果，均不依赖模型自评。
    """

    summary: str = Field(default="", description="一句话总体判断")
    findings: list[InsightFinding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    confidence_basis: str = Field(default="", description="置信度计算依据（服务端填写）")
    validation_warnings: list[str] = Field(
        default_factory=list, description="服务端数值一致性校验告警"
    )


class InsightFeedback(BaseModel):
    """人工反馈：有用 / 无用 / 已采纳。"""

    rating: Literal["useful", "not_useful", "adopted"]
    note: str = Field(default="", max_length=500)


class InsightRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str | None = None
    event_id: str | None = None
    status: str
    model: str
    prompt_version: str
    content: InsightContent | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_ms: int = 0
    cache_hit: bool = False
    feedback: str = ""
    feedback_note: str = ""
    feedback_at: datetime | None = None


class InsightVersionRead(BaseModel):
    """一次成功的解读快照，用于版本回溯与离线评测。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str | None = None
    event_id: str | None = None
    model: str
    prompt_version: str
    content: InsightContent | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_ms: int = 0
    created_at: datetime
