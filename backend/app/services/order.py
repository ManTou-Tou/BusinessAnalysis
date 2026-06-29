"""Order 服务（订单头 + 明细，经 shop 归属到 account）。"""
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.shop import Shop
from app.schemas.order import OrderCreate
from app.services.tenant import get_owned_shop
from app.utils.pagination import keyset_paginate


def _valid_product_ids(db: Session, shop_id: int, product_ids: set[int]) -> set[int]:
    """返回这些 product_id 中确属该 shop 的集合。"""
    if not product_ids:
        return set()
    rows = db.execute(
        select(Product.id).where(
            Product.shop_id == shop_id, Product.id.in_(product_ids)
        )
    ).scalars().all()
    return set(rows)


def create_order(db: Session, account_id: int, payload: OrderCreate) -> Order:
    get_owned_shop(db, account_id, payload.shop_id)

    # 校验明细里引用的 product_id 必须属于本店铺（不属于则拒绝，避免脏关联）。
    referenced = {it.product_id for it in payload.items if it.product_id is not None}
    valid = _valid_product_ids(db, payload.shop_id, referenced)
    invalid = referenced - valid
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"product_id not in shop: {sorted(invalid)}",
        )

    order = Order(
        shop_id=payload.shop_id,
        order_no=payload.order_no,
        order_status=payload.order_status,
        total_amount=payload.total_amount,
        shipping_fee=payload.shipping_fee,
        platform_fee=payload.platform_fee,
        ad_cost=payload.ad_cost,
        external_id=payload.external_id,
        raw_payload=payload.raw_payload,
        items=[
            OrderItem(
                product_id=it.product_id,
                external_product_id=it.external_product_id,
                sku_snapshot=it.sku_snapshot,
                product_name_snapshot=it.product_name_snapshot,
                quantity=it.quantity,
                sale_price=it.sale_price,
                profit=it.profit,
            )
            for it in payload.items
        ],
    )
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="order_no already exists in this shop",
        )
    db.refresh(order)
    return order


def list_orders(
    db: Session,
    account_id: int,
    limit: int,
    cursor: int | None,
    shop_id: int | None = None,
) -> tuple[list[Order], int | None]:
    stmt = (
        select(Order)
        .join(Shop, Order.shop_id == Shop.id)
        .where(Shop.account_id == account_id)
        .options(selectinload(Order.items))
    )
    if shop_id is not None:
        get_owned_shop(db, account_id, shop_id)
        stmt = stmt.where(Order.shop_id == shop_id)
    return keyset_paginate(db, stmt, Order.id, limit, cursor)


def get_order(db: Session, account_id: int, order_id: int) -> Order:
    order = db.execute(
        select(Order)
        .join(Shop, Order.shop_id == Shop.id)
        .where(Order.id == order_id, Shop.account_id == account_id)
        .options(selectinload(Order.items))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
        )
    return order
