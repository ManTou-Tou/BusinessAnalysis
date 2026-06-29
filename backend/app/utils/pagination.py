"""Keyset（游标）分页：避免深 offset 性能问题。

按主键 id 降序翻页，cursor 为上一页最后一条的 id。
"""
from typing import TypeVar

from sqlalchemy import Select
from sqlalchemy.orm import Session

MAX_LIMIT = 100
DEFAULT_LIMIT = 20

T = TypeVar("T")


def keyset_paginate(
    db: Session,
    stmt: Select,
    id_column,
    limit: int,
    cursor: int | None,
) -> tuple[list, int | None]:
    """对 stmt 应用 keyset 分页。

    要求 stmt 选出的实体含 id_column（如 Product.id）。
    返回 (items, next_cursor)；next_cursor 为 None 表示末页。
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = stmt.order_by(id_column.desc())
    if cursor is not None:
        stmt = stmt.where(id_column < cursor)
    rows = db.execute(stmt.limit(limit + 1)).scalars().all()

    has_more = len(rows) > limit
    items = list(rows[:limit])
    next_cursor = items[-1].id if has_more and items else None
    return items, next_cursor
