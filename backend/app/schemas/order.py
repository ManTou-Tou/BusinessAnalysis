from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

OrderStatus = Literal[
    "pending", "paid", "shipped", "completed", "cancelled", "refunded"
]


class OrderItemCreate(BaseModel):
    product_id: int | None = None
    external_product_id: str | None = None
    sku_snapshot: str | None = None
    product_name_snapshot: str | None = None
    quantity: int = Field(default=1, ge=1)
    sale_price: Decimal = Field(ge=0)
    profit: Decimal | None = None


class OrderCreate(BaseModel):
    shop_id: int
    order_no: str
    order_status: OrderStatus = "pending"
    total_amount: Decimal = Field(default=Decimal("0"), ge=0)
    shipping_fee: Decimal = Field(default=Decimal("0"), ge=0)
    platform_fee: Decimal = Field(default=Decimal("0"), ge=0)
    ad_cost: Decimal = Field(default=Decimal("0"), ge=0)
    external_id: str | None = None
    raw_payload: dict[str, Any] | None = None
    items: list[OrderItemCreate] = Field(default_factory=list)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int | None
    external_product_id: str | None
    sku_snapshot: str | None
    product_name_snapshot: str | None
    quantity: int
    sale_price: Decimal
    profit: Decimal | None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shop_id: int
    order_no: str
    order_status: str
    total_amount: Decimal
    shipping_fee: Decimal
    platform_fee: Decimal
    ad_cost: Decimal
    external_id: str | None
    created_at: datetime
    items: list[OrderItemOut] = []
