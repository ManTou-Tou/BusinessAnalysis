"""products 实体导入（Phase 9）：按 (shop_id, sku) upsert（uniq 已存在）。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.models.product import Product
from app.services.imports import common as c

REQUIRED = ("sku", "product_name", "price", "cost")
_ZERO = Decimal("0")


def process_chunk(db, account_id, shop_id, mapping, chunk) -> tuple[int, int, list]:
    errors: list[tuple[int, str]] = []
    parsed: list[dict] = []
    for row_no, row in chunk:
        try:
            parsed.append(
                {
                    "shop_id": shop_id,
                    "sku": c.req_str(c.raw(row, mapping, "sku"), "sku"),
                    "product_name": c.req_str(
                        c.raw(row, mapping, "product_name"), "product_name"
                    ),
                    "price": c.req_decimal(c.raw(row, mapping, "price"), "price"),
                    "cost": c.req_decimal(c.raw(row, mapping, "cost"), "cost"),
                    "stock": c.opt_int(c.raw(row, mapping, "stock"), "stock", default=0),
                    "status": c.opt_str(c.raw(row, mapping, "status")) or "active",
                    "category": c.opt_str(c.raw(row, mapping, "category")),
                }
            )
        except ValueError as exc:
            errors.append((row_no, str(exc)))
    if not parsed:
        return 0, 0, errors

    skus = [r["sku"] for r in parsed]
    existing = set(
        db.execute(
            select(Product.sku).where(Product.shop_id == shop_id, Product.sku.in_(skus))
        ).scalars().all()
    )
    distinct = {r["sku"] for r in parsed}
    updated = len(distinct & existing)
    inserted = len(distinct) - updated

    stmt = mysql_insert(Product).values(parsed)
    db.execute(
        stmt.on_duplicate_key_update(
            product_name=stmt.inserted.product_name,
            price=stmt.inserted.price,
            cost=stmt.inserted.cost,
            stock=stmt.inserted.stock,
            status=stmt.inserted.status,
            category=stmt.inserted.category,
        )
    )
    return inserted, updated, errors
