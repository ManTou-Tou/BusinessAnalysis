"""Analytics 读服务（Phase 6）：商品维度利润聚合（同步只读）。

聚合口径（PHASE_6_PLAN.md §5a#1/#5）：只统计**有成本**（profit IS NOT NULL）的明细。
- units_sold = Σquantity；revenue = Σ(sale_price×quantity)；net_profit = Σprofit。
- cogs = 当前 product.cost × units_sold；gross = revenue − cogs；fees = gross − net。
- break_even_unit_price = (revenue − net_profit) / units_sold（与 (Σcogs+Σallocated)/Σunits 等价、
  且与已存 net 一致；不受成本变更影响）。
> 注：cogs/gross/fees 用**当前** product.cost；若成本在上次 compute 后变化，应以 force=true 重算
> profit-analysis 以保证完全一致（break_even/margin/net 不受影响，仅 cogs/gross/fees 拆分受影响）。
"""
from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.daily_metrics import DailyReport, ShopDailyMetric
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.shop import Shop
from app.schemas.profit import ProductProfitOut
from app.services.profit import q2, q4
from app.services.tenant import get_owned_shop

_ZERO = Decimal("0.00")


def shop_daily(
    db: Session, account_id: int, shop_id: int, d: date_type
) -> ShopDailyMetric:
    """店铺某日数字指标；shop 不属 account 或该日无数据 → 404。"""
    get_owned_shop(db, account_id, shop_id)
    row = db.execute(
        select(ShopDailyMetric).where(
            ShopDailyMetric.shop_id == shop_id, ShopDailyMetric.date == d
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no metrics for date"
        )
    return row


def shop_report(
    db: Session, account_id: int, shop_id: int, d: date_type
) -> DailyReport:
    """店铺某日报告；shop 不属 account 或该日无报告 → 404。"""
    get_owned_shop(db, account_id, shop_id)
    row = db.execute(
        select(DailyReport).where(
            DailyReport.shop_id == shop_id, DailyReport.date == d
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no report for date"
        )
    return row


def product_profit(db: Session, account_id: int, product_id: int) -> ProductProfitOut:
    # 归属校验：product → shop → account
    product = db.execute(
        select(Product)
        .join(Shop, Product.shop_id == Shop.id)
        .where(Product.id == product_id, Shop.account_id == account_id)
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="product not found"
        )

    units_sold, revenue, net_profit = db.execute(
        select(
            func.coalesce(func.sum(OrderItem.quantity), 0),
            func.coalesce(func.sum(OrderItem.sale_price * OrderItem.quantity), _ZERO),
            func.coalesce(func.sum(OrderItem.profit), _ZERO),
        )
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            Order.shop_id == product.shop_id,
            OrderItem.product_id == product_id,
            OrderItem.profit.is_not(None),
        )
    ).one()
    excluded_no_cost = db.execute(
        select(func.count())
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            Order.shop_id == product.shop_id,
            OrderItem.product_id == product_id,
            OrderItem.profit.is_(None),
        )
    ).scalar_one()

    units_sold = int(units_sold)
    revenue = q2(Decimal(revenue))
    net_profit = q2(Decimal(net_profit))
    cogs = q2(product.cost * units_sold)
    gross_profit = q2(revenue - cogs)
    fees = q2(gross_profit - net_profit)
    profit_margin = q4(net_profit / revenue) if revenue > 0 else None
    break_even = q2((revenue - net_profit) / units_sold) if units_sold > 0 else None

    return ProductProfitOut(
        product_id=product_id,
        units_sold=units_sold,
        revenue=revenue,
        cogs=cogs,
        fees=fees,
        gross_profit=gross_profit,
        net_profit=net_profit,
        profit_margin=profit_margin,
        break_even_unit_price=break_even,
        excluded_no_cost=excluded_no_cost,
    )
