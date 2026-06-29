"""orders 实体导入（Phase 9）：一行=一条订单明细，按 order_no 分组。

订单头按 (shop_id, order_no) upsert；明细「先删后插」重建该单（沿用 Phase 7 思路）。
同一 order_no 的明细行必须相邻——非相邻重现由驱动判为整文件失败（见 tasks/imports）。
profit 不在导入写（Phase 6 另算）。
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.models.order import Order, OrderItem
from app.services.imports import common as c

# 明细必填；订单头字段可选（取该组首行）。
REQUIRED = ("order_no", "quantity", "sale_price")
_HEADER_MONEY = ("total_amount", "shipping_fee", "platform_fee", "ad_cost")


def flush_order(db, shop_id, order_no, item_rows, mapping) -> tuple[int, int, list]:
    """处理一个完整订单（item_rows: list[(row_no, row)]）。

    返回 (inserted, updated, [(row_no, msg)...])。不在此 commit。
    """
    errors: list[tuple[int, str]] = []

    # 订单头取该组首行。
    first = item_rows[0][1]
    header = {"order_status": c.opt_str(c.raw(first, mapping, "order_status")) or "pending"}
    try:
        for f in _HEADER_MONEY:
            header[f] = c.opt_decimal(c.raw(first, mapping, f), Decimal("0"))
    except ValueError as exc:
        for rn, _ in item_rows:
            errors.append((rn, f"order header {order_no!r}: {exc}"))
        return 0, 0, errors

    # 解析明细 + 按 shop 解析 product_id（解析不到留快照、product_id 置空）。
    skus = set()
    for _, row in item_rows:
        s = c.opt_str(c.raw(row, mapping, "product_sku"))
        if s:
            skus.add(s)
    sku_to_id: dict[str, int] = {}
    if skus:
        from app.models.product import Product

        sku_to_id = dict(
            db.execute(
                select(Product.sku, Product.id).where(
                    Product.shop_id == shop_id, Product.sku.in_(skus)
                )
            ).all()
        )

    items: list[dict] = []
    for rn, row in item_rows:
        try:
            sku = c.opt_str(c.raw(row, mapping, "product_sku"))
            items.append(
                {
                    "product_id": sku_to_id.get(sku) if sku else None,
                    "sku_snapshot": sku,
                    "quantity": c.req_int(c.raw(row, mapping, "quantity"), "quantity"),
                    "sale_price": c.req_decimal(c.raw(row, mapping, "sale_price"), "sale_price"),
                }
            )
        except ValueError as exc:
            errors.append((rn, str(exc)))

    if not items:
        # 无有效明细：不动存量（不删既有订单），仅报错。
        return 0, 0, errors

    existing_id = db.execute(
        select(Order.id).where(Order.shop_id == shop_id, Order.order_no == order_no)
    ).scalar_one_or_none()

    stmt = mysql_insert(Order).values(
        shop_id=shop_id, order_no=order_no, **header
    )
    db.execute(
        stmt.on_duplicate_key_update(
            order_status=stmt.inserted.order_status,
            total_amount=stmt.inserted.total_amount,
            shipping_fee=stmt.inserted.shipping_fee,
            platform_fee=stmt.inserted.platform_fee,
            ad_cost=stmt.inserted.ad_cost,
        )
    )

    if existing_id is None:
        order_id = db.execute(
            select(Order.id).where(Order.shop_id == shop_id, Order.order_no == order_no)
        ).scalar_one()
        inserted, updated = 1, 0
    else:
        order_id = existing_id
        inserted, updated = 0, 1

    # 明细先删后插（重建该单；profit 不写）。
    db.execute(delete(OrderItem).where(OrderItem.order_id == order_id))
    for it in items:
        it["order_id"] = order_id
    db.execute(insert(OrderItem), items)

    return inserted, updated, errors
