from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProductStatus = Literal["active", "inactive", "out_of_stock"]


class ProductCreate(BaseModel):
    shop_id: int
    product_name: str
    sku: str
    category: str | None = None
    price: Decimal = Field(ge=0)
    cost: Decimal = Field(ge=0)
    stock: int = Field(default=0, ge=0)
    status: ProductStatus = "active"
    external_id: str | None = None


class ProductUpdate(BaseModel):
    """PUT：仅更新提供的字段（shop_id 不可改）。"""

    product_name: str | None = None
    sku: str | None = None
    category: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    cost: Decimal | None = Field(default=None, ge=0)
    stock: int | None = Field(default=None, ge=0)
    status: ProductStatus | None = None
    external_id: str | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shop_id: int
    product_name: str
    sku: str
    category: str | None
    price: Decimal
    cost: Decimal
    stock: int
    status: str
    external_id: str | None
    created_at: datetime
