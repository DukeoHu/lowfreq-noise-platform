from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None

# 解读记录新增列：缓存键、耗时与 usage、人工反馈。两张表结构一致，共用一份定义。
_INSIGHT_COLUMNS = {
    "prompt_hash": "VARCHAR(64) DEFAULT ''",
    "result_hash": "VARCHAR(64) DEFAULT ''",
    "cache_key": "VARCHAR(64) DEFAULT ''",
    "cache_hit": "BOOLEAN DEFAULT 0",
    "prompt_tokens": "INTEGER DEFAULT 0",
    "completion_tokens": "INTEGER DEFAULT 0",
    "total_tokens": "INTEGER DEFAULT 0",
    "elapsed_ms": "INTEGER DEFAULT 0",
    "feedback": "VARCHAR(16) DEFAULT ''",
    "feedback_note": "TEXT DEFAULT ''",
    "feedback_at": "DATETIME",
    "started_at": "DATETIME",
}


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    """SQLite 并发写入：WAL 允许读写并行，busy_timeout 让写锁等待而不是立即报错。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


def is_locked_error(exc: BaseException) -> bool:
    """判断是否为 SQLite 写锁冲突，供短事务重试使用。"""
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        url = settings.resolved_database_url
        if url.startswith("sqlite"):
            _engine = create_engine(
                url,
                connect_args={"check_same_thread": False, "timeout": 30},
                future=True,
            )
            event.listen(_engine, "connect", _configure_sqlite_connection)
        else:
            _engine = create_engine(url, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _session_factory


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from app.db import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=get_engine())
    _ensure_added_columns()


def _ensure_added_columns() -> None:
    """MVP 阶段未引入 Alembic：对已有 SQLite 库补齐后续新增的列。"""
    settings = get_settings()
    if not settings.resolved_database_url.startswith("sqlite"):
        return
    expected = {
        "analysis_runs": {"stage": "VARCHAR(16)"},
        "audit_logs": {"actor": "VARCHAR(64) DEFAULT 'local'"},
        "noise_events": {
            "measurement_point": "VARCHAR(200) DEFAULT ''",
            "equipment": "VARCHAR(200) DEFAULT ''",
            "notes": "TEXT DEFAULT ''",
            "tags": "TEXT DEFAULT '[]'",
            "latitude": "REAL",
            "longitude": "REAL",
        },
        "llm_config": {
            "redact_context": "BOOLEAN DEFAULT 1",
            "api_key_env_only": "BOOLEAN DEFAULT 0",
            "monthly_token_budget": "INTEGER DEFAULT 0",
        },
        "analysis_insights": _INSIGHT_COLUMNS,
        "event_insights": _INSIGHT_COLUMNS,
    }
    with get_engine().begin() as conn:
        for table, columns in expected.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def reset_engine() -> None:
    """测试用：重置引擎与会话工厂，使新的配置生效。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
