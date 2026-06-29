"""Shop 路由。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import PageParams, get_account_id
from app.core.db import get_db
from app.core.ratelimit import RateLimit
from app.schemas.common import Page
from app.schemas.shop import ShopCreate, ShopOut
from app.services import shop as shop_service

router = APIRouter(prefix="/shops", tags=["shops"])


@router.post(
    "",
    response_model=ShopOut,
    status_code=201,
    dependencies=[Depends(RateLimit("shops.create"))],
)
def create_shop(
    payload: ShopCreate,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> ShopOut:
    return shop_service.create_shop(db, account_id, payload)


@router.get("", response_model=Page[ShopOut])
def list_shops(
    params: PageParams = Depends(),
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> Page[ShopOut]:
    items, next_cursor = shop_service.list_shops(
        db, account_id, params.limit, params.cursor
    )
    return Page(items=items, next_cursor=next_cursor)


@router.get("/{shop_id}", response_model=ShopOut)
def get_shop(
    shop_id: int,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> ShopOut:
    return shop_service.get_shop(db, account_id, shop_id)
