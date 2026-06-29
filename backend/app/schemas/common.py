"""通用 schema：keyset 分页响应。"""
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """游标分页结果。next_cursor 为 None 表示已到末页。"""

    items: list[T]
    next_cursor: int | None = None
