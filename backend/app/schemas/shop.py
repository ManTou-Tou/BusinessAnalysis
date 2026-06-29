from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

Platform = Literal["shopee", "tiktok_shop", "lazada"]
ShopStatus = Literal["active", "inactive"]


class ShopCreate(BaseModel):
    platform: Platform
    shop_name: str
    external_shop_id: str | None = None
    status: ShopStatus = "active"


class ShopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    platform: str
    shop_name: str
    external_shop_id: str | None
    status: str
    created_at: datetime
