"""Shop 服务（始终按 account_id 作用域）。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shop import Shop
from app.schemas.shop import ShopCreate
from app.services.tenant import get_owned_shop
from app.utils.pagination import keyset_paginate


def create_shop(db: Session, account_id: int, payload: ShopCreate) -> Shop:
    shop = Shop(
        account_id=account_id,
        platform=payload.platform,
        shop_name=payload.shop_name,
        external_shop_id=payload.external_shop_id,
        status=payload.status,
    )
    db.add(shop)
    db.commit()
    db.refresh(shop)
    return shop


def list_shops(
    db: Session, account_id: int, limit: int, cursor: int | None
) -> tuple[list[Shop], int | None]:
    stmt = select(Shop).where(Shop.account_id == account_id)
    return keyset_paginate(db, stmt, Shop.id, limit, cursor)


def get_shop(db: Session, account_id: int, shop_id: int) -> Shop:
    return get_owned_shop(db, account_id, shop_id)
