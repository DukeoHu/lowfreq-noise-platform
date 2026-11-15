"""审计日志：记录采集、分析、校准、事件变更与导出等关键动作。

操作人取自请求上下文（X-Actor，缺省 local），写入 AuditLog.actor；审计写入失败不阻断业务主流程。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.core.security import current_actor
from app.db.base import get_session_factory, is_locked_error
from app.db.models import AuditLog

logger = logging.getLogger(__name__)

LOCK_RETRIES = 3


def record(
    action: str,
    *,
    entity_type: str = "",
    entity_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    actor = current_actor()
    for attempt in range(LOCK_RETRIES):
        session = get_session_factory()()
        try:
            session.add(
                AuditLog(
                    action=action,
                    actor=actor,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    detail=detail or {},
                )
            )
            session.commit()
            return
        except Exception as exc:  # 审计失败不应影响主流程
            session.rollback()
            if attempt + 1 < LOCK_RETRIES and is_locked_error(exc):
                time.sleep(0.05 * (attempt + 1))
                continue
            logger.exception("failed to write audit log: %s", action)
            return
        finally:
            session.close()
