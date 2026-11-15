from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置。环境变量统一使用 LOWFREQ_ 前缀。"""

    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", env_prefix="LOWFREQ_", extra="ignore")

    app_name: str = "低频噪音 Web 平台 API"
    api_prefix: str = "/api"
    data_dir: Path = Path("./data")
    database_url: str = ""
    max_upload_mb: int = 500
    # 内网共享口令：留空表示不鉴权；设置后 HTTP/WebSocket 均需携带
    access_token: str = ""
    # 单个音频参与离线分析的时长上限（秒）：0 表示不限制。
    # 默认 600 s 用于约束解码与频谱计算的内存峰值，低频测量通常远短于此。
    analysis_max_seconds: int = 600
    allowed_extensions: str = ".wav,.flac,.ogg,.mp3"
    cors_origins: str = "http://localhost:5173"
    default_calibration_offset_db: float = 0.0
    analysis_workers: int = 1
    audio_retention_days: int = 0
    retention_sweep_hours: int = 24
    disk_warn_percent: float = 85.0

    # LLM 辅助解读：默认关闭。兼容 OpenAI Chat Completions 协议的云厂商/网关。
    llm_enabled: bool = False
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_s: float = 60.0
    llm_max_tokens: int = 1500
    # 分析成功后自动生成 AI 解读（默认关闭，避免无谓调用与等待）
    llm_auto_insight: bool = False
    # 摘要脱敏：默认不向模型发送文件名、位置、备注、设备等现场信息
    llm_redact_context: bool = True
    # API Key 仅保存在环境变量中（数据库不落盘）；由设置页开关控制
    llm_api_key_env_only: bool = False
    # 月度 token 预算：0 表示不限额
    llm_monthly_token_budget: int = 0
    # 单次解读的总超时（秒）：包含重试与预算翻倍，超过后标记失败
    llm_insight_timeout_s: float = 180.0

    @property
    def resolved_data_dir(self) -> Path:
        path = self.data_dir if self.data_dir.is_absolute() else BACKEND_ROOT / self.data_dir
        return path.resolve()

    @property
    def audio_dir(self) -> Path:
        return self.resolved_data_dir / "audio"

    @property
    def result_dir(self) -> Path:
        return self.resolved_data_dir / "results"

    @property
    def photos_dir(self) -> Path:
        return self.resolved_data_dir / "photos"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.resolved_data_dir / 'lowfreq.db').as_posix()}"

    @property
    def allowed_extension_set(self) -> set[str]:
        return {item.strip().lower() for item in self.allowed_extensions.split(",") if item.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def ensure_dirs(self) -> None:
        for path in (self.resolved_data_dir, self.audio_dir, self.result_dir, self.photos_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
