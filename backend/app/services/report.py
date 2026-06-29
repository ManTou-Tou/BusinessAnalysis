"""日报确定性聚合（Phase 7）。

给定 shop_id + date：用 SQL 聚合出店铺/商品指标、top/low、负面评论计数与样本——
**全部数值与排名在此确定性产出**，LLM 不参与（§5a#1）。选取/排名逻辑抽成纯函数便于单测。

关键口径（§5a#5）：营收/订单/销量/广告费来自当天全部合格订单/明细（不带 profit 过滤）；
profit 只 Σ 非空 `order_items.profit`；覆盖用明细级 items_total / profit_items_covered。
日期用半开区间 `created_at >= date AND < date+1`（§5a#8）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.review import Review
from app.services.profit import q2, q4

_ZERO = Decimal("0.00")


@dataclass
class ProductAgg:
    product_id: int
    product_name: str
    units_sold: int
    revenue: Decimal
    profit: Decimal  # Σ 非空 profit
    items: int  # 当天该商品明细数
    covered_items: int  # 其中 profit 非空数

    @property
    def fully_covered(self) -> bool:
        return self.items > 0 and self.covered_items == self.items

    def margin(self) -> Decimal | None:
        """仅在完全覆盖且收入>0 时给出净利率，否则 None（数据不足，不参与低利润排名）。"""
        if not self.fully_covered or self.revenue <= 0:
            return None
        return q4(self.profit / self.revenue)


@dataclass
class ReportFacts:
    shop_id: int
    date: date_type
    orders: int
    revenue: Decimal
    profit: Decimal
    ad_spend: Decimal
    items_total: int
    profit_items_covered: int
    products: list[ProductAgg]
    top_products: list[ProductAgg]
    low_profit_products: list[ProductAgg]
    total_reviews: int
    negative_count: int
    negative_by_type: dict[str, int]
    negative_samples: list[str] = field(default_factory=list)


# ----------------------------- 纯函数（可单测，无 DB） -----------------------------


def select_top_products(products: list[ProductAgg], n: int) -> list[ProductAgg]:
    """按营收降序取前 N；营收相同按 product_id 升序（确定性 tie-break）。"""
    ranked = sorted(products, key=lambda p: (-p.revenue, p.product_id))
    return ranked[:n]


def select_low_profit_products(
    products: list[ProductAgg], margin_threshold: Decimal, n: int
) -> list[ProductAgg]:
    """净利率 < 阈值的低利润商品，按净利率升序取前 N。

    只纳入**完全覆盖且收入>0**的商品（margin 可算）；数据不足者不臆断为低利润。
    """
    eligible = [
        p for p in products if (m := p.margin()) is not None and m < margin_threshold
    ]
    eligible.sort(key=lambda p: (p.margin(), p.product_id))
    return eligible[:n]


# ----------------------------- DB 聚合 -----------------------------


def _day_bounds(d: date_type) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min)
    return start, start + timedelta(days=1)


def aggregate(db: Session, shop_id: int, d: date_type) -> ReportFacts:
    start, end = _day_bounds(d)
    in_day = (Order.created_at >= start, Order.created_at < end)
    # 评论按 Review.created_at 过滤（不可复用基于 Order.created_at 的 in_day——
    # 否则引用 Order.* 会把 orders 隐式并入 FROM 形成笛卡尔积，且按订单日期而非评论日期过滤）。
    review_in_day = (Review.created_at >= start, Review.created_at < end)

    # 店铺订单数 + 广告费（订单级，不带 profit 过滤）
    orders_count, ad_spend = db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.ad_cost), _ZERO),
        ).where(Order.shop_id == shop_id, *in_day)
    ).one()

    # 店铺明细级：items_total / revenue / profit(Σ非空) / covered(count非空)
    items_total, revenue, profit, covered = db.execute(
        select(
            func.count(OrderItem.id),
            func.coalesce(func.sum(OrderItem.sale_price * OrderItem.quantity), _ZERO),
            func.coalesce(func.sum(OrderItem.profit), _ZERO),  # SUM 自动忽略 NULL
            func.count(OrderItem.profit),  # COUNT 自动忽略 NULL
        )
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(Order.shop_id == shop_id, *in_day)
    ).one()

    # 商品级聚合（product_id 非空，inner join 自动排除无商品明细）
    product_rows = db.execute(
        select(
            OrderItem.product_id,
            Product.product_name,
            func.coalesce(func.sum(OrderItem.quantity), 0),
            func.coalesce(func.sum(OrderItem.sale_price * OrderItem.quantity), _ZERO),
            func.coalesce(func.sum(OrderItem.profit), _ZERO),
            func.count(OrderItem.id),
            func.count(OrderItem.profit),
        )
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .join(Product, OrderItem.product_id == Product.id)
        .where(Order.shop_id == shop_id, *in_day)
        .group_by(OrderItem.product_id, Product.product_name)
    ).all()
    products = [
        ProductAgg(
            product_id=pid,
            product_name=name,
            units_sold=int(units),
            revenue=q2(Decimal(rev)),
            profit=q2(Decimal(prof)),
            items=int(items),
            covered_items=int(cov),
        )
        for pid, name, units, rev, prof, items, cov in product_rows
    ]

    # 评论：总数、负面数、负面按类型计数、负面样本
    total_reviews, negative_count = db.execute(
        select(
            func.count(Review.id),
            func.coalesce(
                func.sum(func.if_(Review.sentiment == "negative", 1, 0)), 0
            ),
        ).where(Review.shop_id == shop_id, *review_in_day)
    ).one()
    neg_type_rows = db.execute(
        select(Review.review_type, func.count(Review.id))
        .where(Review.shop_id == shop_id, Review.sentiment == "negative", *review_in_day)
        .group_by(Review.review_type)
    ).all()
    negative_by_type = {(rt or "unknown"): int(c) for rt, c in neg_type_rows}
    sample_rows = db.execute(
        select(Review.review_text)
        .where(Review.shop_id == shop_id, Review.sentiment == "negative", *review_in_day)
        .order_by(Review.id)
        .limit(settings.report_negative_sample)
    ).scalars().all()
    negative_samples = [
        (t or "")[: settings.review_text_max_chars] for t in sample_rows
    ]

    threshold = Decimal(str(settings.report_low_profit_margin))
    return ReportFacts(
        shop_id=shop_id,
        date=d,
        orders=int(orders_count),
        revenue=q2(Decimal(revenue)),
        profit=q2(Decimal(profit)),
        ad_spend=q2(Decimal(ad_spend)),
        items_total=int(items_total),
        profit_items_covered=int(covered),
        products=products,
        top_products=select_top_products(products, settings.report_top_n),
        low_profit_products=select_low_profit_products(
            products, threshold, settings.report_top_n
        ),
        total_reviews=int(total_reviews),
        negative_count=int(negative_count),
        negative_by_type=negative_by_type,
        negative_samples=negative_samples,
    )


# ----------------------------- 投影：sections（落库/对外）与 agent 输入 -----------------------------


def _product_brief(p: ProductAgg) -> dict:
    margin = p.margin()
    return {
        "product_id": p.product_id,
        "product_name": p.product_name,
        "units_sold": p.units_sold,
        "revenue": str(p.revenue),
        "profit": str(p.profit) if p.fully_covered else None,
        "profit_margin": str(margin) if margin is not None else None,
    }


def build_sections(facts: ReportFacts) -> dict:
    """确定性结构化 sections（存 daily_reports.sections_json，对外返回；不经 LLM）。"""
    return {
        "top_products": [_product_brief(p) for p in facts.top_products],
        "low_profit_products": [_product_brief(p) for p in facts.low_profit_products],
        "negative_review_summary": {
            "total_reviews": facts.total_reviews,
            "negative_count": facts.negative_count,
            "by_type": facts.negative_by_type,
        },
        "profit_coverage": {
            "items_total": facts.items_total,
            "profit_items_covered": facts.profit_items_covered,
        },
    }


def build_agent_input(facts: ReportFacts) -> dict:
    """喂给 LLM 的「只读事实」（数字 + 负面样本）；LLM 只据此写散文，不得改写数字。"""
    return {
        "date": facts.date.isoformat(),
        "metrics": {
            "orders": facts.orders,
            "revenue": str(facts.revenue),
            "profit": str(facts.profit),
            "ad_spend": str(facts.ad_spend),
            "items_total": facts.items_total,
            "profit_items_covered": facts.profit_items_covered,
        },
        "sections": build_sections(facts),
        "negative_samples": facts.negative_samples,
    }
