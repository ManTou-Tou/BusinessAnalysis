"""reviews 实体导入（Phase 9）：**insert-only**（append，无天然唯一键、不 upsert）。

product_sku → product_id（按 shop 内解析）；order_no（可选）→ order_id。解析不到 → 行错误。
重试安全靠驱动的断点续跑（已提交行偏移不再处理），不靠 upsert。
"""
from __future__ import annotations

from sqlalchemy import insert, select

from app.models.order import Order
from app.models.product import Product
from app.models.review import Review
from app.services.imports import common as c

REQUIRED = ("product_sku", "review_text")


def process_chunk(db, account_id, shop_id, mapping, chunk) -> tuple[int, int, list]:
    errors: list[tuple[int, str]] = []
    staged: list[tuple[int, dict]] = []  # (row_no, partial fields incl. sku/order_no)
    for row_no, row in chunk:
        try:
            staged.append(
                (
                    row_no,
                    {
                        "sku": c.req_str(c.raw(row, mapping, "product_sku"), "product_sku"),
                        "order_no": c.opt_str(c.raw(row, mapping, "order_no")),
                        "buyer_name": c.opt_str(c.raw(row, mapping, "buyer_name")),
                        "rating": c.rating(c.raw(row, mapping, "rating")),
                        "review_text": c.req_str(
                            c.raw(row, mapping, "review_text"), "review_text"
                        ),
                    },
                )
            )
        except ValueError as exc:
            errors.append((row_no, str(exc)))
    if not staged:
        return 0, 0, errors

    # 批量解析外键（按 shop 限定，禁跨租户）。
    skus = {s["sku"] for _, s in staged}
    sku_to_id = dict(
        db.execute(
            select(Product.sku, Product.id).where(
                Product.shop_id == shop_id, Product.sku.in_(skus)
            )
        ).all()
    )
    order_nos = {s["order_no"] for _, s in staged if s["order_no"]}
    no_to_id = (
        dict(
            db.execute(
                select(Order.order_no, Order.id).where(
                    Order.shop_id == shop_id, Order.order_no.in_(order_nos)
                )
            ).all()
        )
        if order_nos
        else {}
    )

    rows: list[dict] = []
    for row_no, s in staged:
        pid = sku_to_id.get(s["sku"])
        if pid is None:
            errors.append((row_no, f"product_sku not found in shop: {s['sku']!r}"))
            continue
        oid = None
        if s["order_no"]:
            oid = no_to_id.get(s["order_no"])
            if oid is None:
                errors.append((row_no, f"order_no not found in shop: {s['order_no']!r}"))
                continue
        rows.append(
            {
                "shop_id": shop_id,
                "product_id": pid,
                "order_id": oid,
                "buyer_name": s["buyer_name"],
                "rating": s["rating"],
                "review_text": s["review_text"],
            }
        )

    if rows:
        db.execute(insert(Review), rows)
    return len(rows), 0, errors
