"""多租户硬约束：所有业务访问必须经 account_id -> shop_id 校验。"""
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shop import Shop


def get_owned_shop(db: Session, account_id: int, shop_id: int) -> Shop:
    """返回归属该 account 的 shop；否则 404（不泄露是否存在）。"""
    shop = db.execute(
        select(Shop).where(Shop.id == shop_id, Shop.account_id == account_id)
    ).scalar_one_or_none()
    if shop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="shop not found"
        )
    return shop
