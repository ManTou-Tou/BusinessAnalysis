"""Order 路由。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import PageParams, get_account_id
from app.core.db import get_db
from app.core.ratelimit import RateLimit
from app.schemas.common import Page
from app.schemas.order import OrderCreate, OrderOut
from app.services import order as order_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    response_model=OrderOut,
    status_code=201,
    dependencies=[Depends(RateLimit("orders.create"))],
)
def create_order(
    payload: OrderCreate,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> OrderOut:
    return order_service.create_order(db, account_id, payload)


@router.get("", response_model=Page[OrderOut])
def list_orders(
    params: PageParams = Depends(),
    shop_id: int | None = Query(default=None),
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> Page[OrderOut]:
    items, next_cursor = order_service.list_orders(
        db, account_id, params.limit, params.cursor, shop_id
    )
    return Page(items=items, next_cursor=next_cursor)


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> OrderOut:
    return order_service.get_order(db, account_id, order_id)
