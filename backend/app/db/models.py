from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Table, Column, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


event_analysis_link = Table(
    "event_analysis_links",
    Base.metadata,
    Column("event_id", String(32), ForeignKey("noise_events.id", ondelete="CASCADE"), primary_key=True),
    Column("analysis_run_id", String(32), ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True),
)


class AudioFile(Base):
    __tablename__ = "audio_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512), comment="相对 data_dir 的路径")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    analyses: Mapped[list["AnalysisRun"]] = relationship(back_populates="audio_file", cascade="all, delete-orphan")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    audio_file_id: Mapped[str] = mapped_column(String(32), ForeignKey("audio_files.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="整改阶段：before/off/after")
    algorithm_version: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict] = mapped_column(JSON)
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    audio_file: Mapped[AudioFile] = relationship(back_populates="analyses")
    events: Mapped[list["NoiseEvent"]] = relationship(secondary=event_analysis_link, back_populates="analyses")
    insight: Mapped["AnalysisInsight | None"] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )


class AnalysisInsight(Base):
    """LLM 对单次分析结果的辅助解读；与 DSP 结果分离存储，可重复生成。"""

    __tablename__ = "analysis_insights"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("analysis_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    model: Mapped[str] = mapped_column(String(80), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")
    result_hash: Mapped[str] = mapped_column(String(64), default="")
    cache_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    feedback: Mapped[str] = mapped_column(String(16), default="")
    feedback_note: Mapped[str] = mapped_column(Text, default="")
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[AnalysisRun] = relationship(back_populates="insight")


class NoiseEvent(Base):
    __tablename__ = "noise_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    measurement_point: Mapped[str] = mapped_column(String(200), default="")
    equipment: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    analyses: Mapped[list[AnalysisRun]] = relationship(secondary=event_analysis_link, back_populates="events")
    photos: Mapped[list["EventPhoto"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventPhoto.created_at"
    )
    insight: Mapped["EventInsight | None"] = relationship(
        back_populates="event", cascade="all, delete-orphan", uselist=False
    )


class EventInsight(Base):
    """LLM 对事件整改前后（before/off/after）对比的辅助解读。"""

    __tablename__ = "event_insights"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("noise_events.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    model: Mapped[str] = mapped_column(String(80), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")
    result_hash: Mapped[str] = mapped_column(String(64), default="")
    cache_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    feedback: Mapped[str] = mapped_column(String(16), default="")
    feedback_note: Mapped[str] = mapped_column(Text, default="")
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    event: Mapped[NoiseEvent] = relationship(back_populates="insight")


class InsightVersion(Base):
    """每次成功生成都追加一条快照，支持历史回溯与效果对比。"""

    __tablename__ = "insight_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("noise_events.id", ondelete="CASCADE"), nullable=True, index=True
    )
    model: Mapped[str] = mapped_column(String(80), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class EventPhoto(Base):
    __tablename__ = "event_photos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String(32), ForeignKey("noise_events.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512), comment="相对 data_dir 的路径")
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    event: Mapped[NoiseEvent] = relationship(back_populates="photos")


class Measurement(Base):
    """一次实时测量会话；重连时按 session_id 归并到同一条记录。"""

    __tablename__ = "measurements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    device_label: Mapped[str] = mapped_column(String(200), default="")
    sample_rate: Mapped[int] = mapped_column(Integer, default=0)
    offset_db: Mapped[float] = mapped_column(Float, default=0.0)
    full_scale_pa: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    point_count: Mapped[int] = mapped_column(Integer, default=0)
    capture: Mapped[dict] = mapped_column(JSON, default=dict)

    segments: Mapped[list["MeasurementSegment"]] = relationship(
        back_populates="measurement", cascade="all, delete-orphan", order_by="MeasurementSegment.segment_index"
    )
    metric_points: Mapped[list["MetricPoint"]] = relationship(
        back_populates="measurement", cascade="all, delete-orphan"
    )


class MeasurementSegment(Base):
    """测量过程中归档的录音分段，与离线分析任务一一对应。"""

    __tablename__ = "measurement_segments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    measurement_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("measurements.id", ondelete="CASCADE"), index=True
    )
    segment_index: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    audio_file_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    measurement: Mapped[Measurement] = relationship(back_populates="segments")


class MetricPoint(Base):
    """1 Hz 指标序列；t_offset_s 为相对测量开始的整秒偏移。"""

    __tablename__ = "metric_points"
    __table_args__ = (Index("ix_metric_points_measurement_time", "measurement_id", "t_offset_s"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    measurement_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("measurements.id", ondelete="CASCADE"), index=True
    )
    t_offset_s: Mapped[int] = mapped_column(Integer)
    laeq: Mapped[float | None] = mapped_column(Float, nullable=True)
    lceq: Mapped[float | None] = mapped_column(Float, nullable=True)
    lzeq: Mapped[float | None] = mapped_column(Float, nullable=True)
    laf: Mapped[float | None] = mapped_column(Float, nullable=True)
    lcf: Mapped[float | None] = mapped_column(Float, nullable=True)
    lafmax: Mapped[float | None] = mapped_column(Float, nullable=True)
    lcfmax: Mapped[float | None] = mapped_column(Float, nullable=True)
    lcpeak: Mapped[float | None] = mapped_column(Float, nullable=True)
    c_minus_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    measurement: Mapped[Measurement] = relationship(back_populates="metric_points")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    action: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(64), default="local")
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class LlmConfig(Base):
    """LLM 配置单例（id=1）；未写入时回退到环境变量。密钥不回传给前端。"""

    __tablename__ = "llm_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    base_url: Mapped[str] = mapped_column(String(255), default="")
    api_key: Mapped[str] = mapped_column(String(512), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    timeout_s: Mapped[float] = mapped_column(Float, default=60.0)
    max_tokens: Mapped[int] = mapped_column(Integer, default=1500)
    auto_insight: Mapped[bool] = mapped_column(Boolean, default=False)
    # 脱敏：默认不向模型发送文件名、位置、备注、设备等现场信息
    redact_context: Mapped[bool] = mapped_column(Boolean, default=True)
    # 仅环境变量模式：数据库不保存 API Key，运行时读取 LOWFREQ_LLM_API_KEY
    api_key_env_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # 月度 token 预算：0 表示不限额
    monthly_token_budget: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    updated_by: Mapped[str] = mapped_column(String(64), default="local")
