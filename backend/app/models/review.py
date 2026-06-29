"""评论：review（分类字段在 Phase 5 由 Agent 填充）。"""
from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import CreatedAtMixin


class Review(CreatedAtMixin, Base):
    __tablename__ = "reviews"
    __table_args__ = (
        Index("ix_reviews_product_created", "product_id", "created_at"),
        Index("ix_reviews_shop_type", "shop_id", "review_type"),
        Index("ix_reviews_sentiment", "sentiment"),
        # Phase 8（迁移 0004）：keyset / 批量分类 / 日报聚合的覆盖排序索引。
        Index("ix_reviews_shop_id", "shop_id", "id"),
        Index("ix_reviews_product_id", "product_id", "id"),
        Index("ix_reviews_shop_type_id", "shop_id", "review_type", "id"),
        Index("ix_reviews_shop_created", "shop_id", "created_at"),
        Index("ix_reviews_shop_sentiment_created", "shop_id", "sentiment", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id"), nullable=False
    )
    # order_id 可空：很多平台评论没有本地 order 映射。
    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("orders.id"), nullable=True
    )
    buyer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 以下由 Review Classifier Agent 填充（Phase 5）
    review_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
