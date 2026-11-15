from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    actor: str = "local"
    entity_type: str
    entity_id: str | None = None
    detail: dict = {}
    created_at: datetime
