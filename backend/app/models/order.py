"""订单：order + order_items（订单头与明细分离）。"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import CreatedAtMixin, Money


class Order(CreatedAtMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("shop_id", "order_no", name="uq_orders_shop_order_no"),
        Index("ix_orders_shop_created", "shop_id", "created_at"),
        # Phase 8（迁移 0004）：keyset 列表 WHERE shop_id=? ORDER BY id DESC
        # （(shop_id,created_at) 不能服务 ORDER BY id）。
        Index("ix_orders_shop_id", "shop_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id"), nullable=False
    )
    order_no: Mapped[str] = mapped_column(String(128), nullable=False)
    order_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending/paid/shipped/completed/cancelled/refunded
    total_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=0)
    shipping_fee: Mapped[Decimal] = mapped_column(Money, nullable=False, default=0)
    platform_fee: Mapped[Decimal] = mapped_column(Money, nullable=False, default=0)
    ad_cost: Mapped[Decimal] = mapped_column(Money, nullable=False, default=0)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        Index("ix_order_items_order", "order_id"),
        Index("ix_order_items_product", "product_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id"), nullable=False
    )
    # product_id 可空：历史订单可能商品未入库或被删除，靠快照字段兜底。
    product_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("products.id"), nullable=True
    )
    external_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sku_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    product_name_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sale_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    profit: Mapped[Decimal | None] = mapped_column(Money, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="items")
