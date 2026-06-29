"""Daily Report / shop analytics 相关 schema（Phase 7）。"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class DailyReportRequest(BaseModel):
    """触发日报生成（确定性聚合 + LLM 总结，异步）。"""

    shop_id: int
    date: date_type | None = None  # 默认昨天（UTC）
    force: bool = False  # 强制重算（追加 uniquifier 新建任务）


class ShopDailyMetricOut(BaseModel):
    """店铺某日数字指标（确定性）。"""

    model_config = ConfigDict(from_attributes=True)

    shop_id: int
    date: date_type
    orders: int
    revenue: Decimal
    profit: Decimal
    ad_spend: Decimal
    items_total: int
    profit_items_covered: int
    views: int | None
    clicks: int | None
    conversion_rate: Decimal | None


class DailyReportOut(BaseModel):
    """店铺某日报告（确定性 sections + LLM 散文）。"""

    model_config = ConfigDict(from_attributes=True)

    shop_id: int
    date: date_type
    daily_summary: str | None
    recommended_actions: list[str] | None
    sections_json: dict[str, Any] | None
    model: str | None
    prompt_version: str | None
    generated_at: datetime
