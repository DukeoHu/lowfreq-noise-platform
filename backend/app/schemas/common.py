from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """统一分页响应：条目 + 总数，便于前端显示页码与总数。"""

    items: list[T]
    total: int
    limit: int
    offset: int
