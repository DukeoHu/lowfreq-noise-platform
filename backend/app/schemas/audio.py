from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AudioFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    size_bytes: int
    sample_rate: int | None = None
    channels: int | None = None
    duration_s: float | None = None
    sha256: str
    created_at: datetime
