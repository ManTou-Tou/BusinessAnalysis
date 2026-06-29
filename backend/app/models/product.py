"""商品：product。"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Money, TimestampMixin


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("shop_id", "sku", name="uq_products_shop_sku"),
        Index("ix_products_shop_status", "shop_id", "status"),
        # Phase 8（迁移 0004）：keyset 列表 WHERE shop_id=? ORDER BY id DESC。
        Index("ix_products_shop_id", "shop_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id"), nullable=False
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Money, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active"
    )  # active/inactive/out_of_stock
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
