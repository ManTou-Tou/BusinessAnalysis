"""每日指标与日报（Phase 7）。

- shop_daily_metrics / product_daily_metrics：**确定性数字**（SQL 聚合落库）。
- daily_reports：**LLM 生成的散文报告** + 确定性结构化 sections（与数字分表，生命周期不同）。
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Money


class ShopDailyMetric(Base):
    __tablename__ = "shop_daily_metrics"
    __table_args__ = (
        UniqueConstraint("shop_id", "date", name="uq_shop_daily_shop_date"),
        Index("ix_shop_daily_date", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue: Mapped[Decimal] = mapped_column(Money, nullable=False, default=0)
    profit: Mapped[Decimal] = mapped_column(Money, nullable=False, default=0)
    ad_spend: Mapped[Decimal] = mapped_column(Money, nullable=False, default=0)
    # 明细级利润覆盖（§5a#5）：未覆盖明细不当 0
    items_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profit_items_covered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 「新任务赢」守卫：与 daily_reports 一致，并发 force 下旧任务不得覆盖新指标
    source_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 暂无数据源 → 留 NULL，不假造（§5a#2）
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversion_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)


class ProductDailyMetric(Base):
    __tablename__ = "product_daily_metrics"
    __table_args__ = (
        UniqueConstraint("product_id", "date", name="uq_product_daily_product_date"),
        Index("ix_product_daily_shop_date", "shop_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    units_sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue: Mapped[Decimal] = mapped_column(Money, nullable=False, default=0)
    profit: Mapped[Decimal] = mapped_column(Money, nullable=False, default=0)


class DailyReport(Base):
    __tablename__ = "daily_reports"
    __table_args__ = (
        UniqueConstraint("shop_id", "date", name="uq_daily_reports_shop_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    daily_summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # LLM 散文
    recommended_actions: Mapped[list | None] = mapped_column(JSON, nullable=True)  # LLM 散文条目
    sections_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 确定性 sections（不含 LLM）
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 乱序覆盖防护（§5a#6）：旧 source_task_id 不得覆盖新报告
    source_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
