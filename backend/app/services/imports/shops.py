"""shops 实体导入（Phase 9）：account 级，按 (account_id, external_shop_id) upsert。

external_shop_id 必填非空（唯一键依赖，迁移 0005）。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.models.shop import Shop
from app.services.imports import common as c

REQUIRED = ("external_shop_id", "platform", "shop_name")


def process_chunk(db, account_id, shop_id, mapping, chunk) -> tuple[int, int, list]:
    """chunk: list[(row_no, row_tuple)]。返回 (inserted, updated, [(row_no, msg)...])。

    不在此 commit——由驱动调用 update_import_progress 同事务提交。
    """
    errors: list[tuple[int, str]] = []
    parsed: list[dict] = []
    for row_no, row in chunk:
        try:
            ext = c.req_str(c.raw(row, mapping, "external_shop_id"), "external_shop_id")
            parsed.append(
                {
                    "account_id": account_id,
                    "external_shop_id": ext,
                    "platform": c.req_str(c.raw(row, mapping, "platform"), "platform"),
                    "shop_name": c.req_str(c.raw(row, mapping, "shop_name"), "shop_name"),
                    "status": c.opt_str(c.raw(row, mapping, "status")) or "active",
                }
            )
        except ValueError as exc:
            errors.append((row_no, str(exc)))
    if not parsed:
        return 0, 0, errors

    keys = [r["external_shop_id"] for r in parsed]
    existing = set(
        db.execute(
            select(Shop.external_shop_id).where(
                Shop.account_id == account_id, Shop.external_shop_id.in_(keys)
            )
        ).scalars().all()
    )
    distinct = {r["external_shop_id"] for r in parsed}
    updated = len(distinct & existing)
    inserted = len(distinct) - updated

    stmt = mysql_insert(Shop).values(parsed)
    db.execute(
        stmt.on_duplicate_key_update(
            platform=stmt.inserted.platform,
            shop_name=stmt.inserted.shop_name,
            status=stmt.inserted.status,
        )
    )
    return inserted, updated, errors
