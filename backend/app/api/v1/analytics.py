"""Analytics 读路由（Phase 6 商品利润 + Phase 7 店铺日指标/日报，均同步只读）。"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_account_id
from app.core.db import get_db
from app.schemas.profit import ProductProfitOut
from app.schemas.report import DailyReportOut, ShopDailyMetricOut
from app.services import analytics as analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _default_date(d: date_type | None) -> date_type:
    return d or (datetime.now(timezone.utc).date() - timedelta(days=1))


@router.get("/product/{product_id}/profit", response_model=ProductProfitOut)
def product_profit(
    product_id: int,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> ProductProfitOut:
    return analytics_service.product_profit(db, account_id, product_id)


@router.get("/shop/{shop_id}/daily", response_model=ShopDailyMetricOut)
def shop_daily(
    shop_id: int,
    date: date_type | None = Query(default=None),
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> ShopDailyMetricOut:
    return analytics_service.shop_daily(db, account_id, shop_id, _default_date(date))


@router.get("/shop/{shop_id}/report", response_model=DailyReportOut)
def shop_report(
    shop_id: int,
    date: date_type | None = Query(default=None),
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> DailyReportOut:
    return analytics_service.shop_report(db, account_id, shop_id, _default_date(date))
