"""Product 服务（经 shop 归属到 account）。"""
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.shop import Shop
from app.schemas.product import ProductCreate, ProductUpdate
from app.services.tenant import get_owned_shop
from app.utils.pagination import keyset_paginate


def _get_owned_product(db: Session, account_id: int, product_id: int) -> Product:
    """加载归属该 account 的商品（经 shop 关联校验）。"""
    product = db.execute(
        select(Product)
        .join(Shop, Product.shop_id == Shop.id)
        .where(Product.id == product_id, Shop.account_id == account_id)
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="product not found"
        )
    return product


def create_product(db: Session, account_id: int, payload: ProductCreate) -> Product:
    get_owned_shop(db, account_id, payload.shop_id)  # 校验店铺归属
    product = Product(**payload.model_dump())
    db.add(product)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="product sku already exists in this shop",
        )
    db.refresh(product)
    return product


def list_products(
    db: Session,
    account_id: int,
    limit: int,
    cursor: int | None,
    shop_id: int | None = None,
) -> tuple[list[Product], int | None]:
    stmt = (
        select(Product)
        .join(Shop, Product.shop_id == Shop.id)
        .where(Shop.account_id == account_id)
    )
    if shop_id is not None:
        get_owned_shop(db, account_id, shop_id)
        stmt = stmt.where(Product.shop_id == shop_id)
    return keyset_paginate(db, stmt, Product.id, limit, cursor)


def get_product(db: Session, account_id: int, product_id: int) -> Product:
    return _get_owned_product(db, account_id, product_id)


def update_product(
    db: Session, account_id: int, product_id: int, payload: ProductUpdate
) -> Product:
    product = _get_owned_product(db, account_id, product_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="product sku already exists in this shop",
        )
    db.refresh(product)
    return product
