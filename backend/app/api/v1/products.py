"""Product 路由。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import PageParams, get_account_id
from app.core.db import get_db
from app.core.ratelimit import RateLimit
from app.schemas.common import Page
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate
from app.services import product as product_service

router = APIRouter(prefix="/products", tags=["products"])


@router.post(
    "",
    response_model=ProductOut,
    status_code=201,
    dependencies=[Depends(RateLimit("products.create"))],
)
def create_product(
    payload: ProductCreate,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> ProductOut:
    return product_service.create_product(db, account_id, payload)


@router.get("", response_model=Page[ProductOut])
def list_products(
    params: PageParams = Depends(),
    shop_id: int | None = Query(default=None),
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> Page[ProductOut]:
    items, next_cursor = product_service.list_products(
        db, account_id, params.limit, params.cursor, shop_id
    )
    return Page(items=items, next_cursor=next_cursor)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: int,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> ProductOut:
    return product_service.get_product(db, account_id, product_id)


@router.put(
    "/{product_id}",
    response_model=ProductOut,
    dependencies=[Depends(RateLimit("products.update"))],
)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> ProductOut:
    return product_service.update_product(db, account_id, product_id, payload)
