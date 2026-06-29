"""Profit / Analytics 相关 schema（Phase 6）。"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class ProfitAnalysisRequest(BaseModel):
    """触发利润计算（确定性，异步）。"""

    shop_id: int
    order_id: int | None = None  # 给则只算该单；不给则算本店铺（受单次上限约束）
    force: bool = False  # 强制重算（追加 uniquifier 新建任务）；默认 false 走幂等复用


class ProductProfitOut(BaseModel):
    """某商品的利润聚合（仅统计已计算/有成本的明细）。"""

    product_id: int
    units_sold: int
    revenue: Decimal
    cogs: Decimal
    fees: Decimal  # 分摊到该商品的费用（= gross − net）
    gross_profit: Decimal
    net_profit: Decimal
    profit_margin: Decimal | None  # revenue==0 时为 null
    break_even_unit_price: Decimal | None  # units_sold==0 时为 null
    excluded_no_cost: int  # 因成本缺失（profit IS NULL）被排除的明细数
