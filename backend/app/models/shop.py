"""店铺：shop（归属 account，平台维度）。"""
from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin


class Shop(TimestampMixin, Base):
    __tablename__ = "shops"
    __table_args__ = (
        Index("ix_shops_account_id", "account_id"),
        # Phase 9（迁移 0005）：Excel 导入按 (account_id, external_shop_id) upsert 的稳定键。
        UniqueConstraint("account_id", "external_shop_id", name="uq_shops_account_external"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)  # shopee/tiktok_shop/lazada
    shop_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_shop_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
