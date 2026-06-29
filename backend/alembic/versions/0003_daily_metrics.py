"""daily metrics & reports: shop/product daily metrics + daily_reports

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-20

Phase 7：每日确定性指标（shop/product_daily_metrics）与 LLM 散文报告（daily_reports）。
带 FK（租户/清理）与 uniq(shop_id|product_id, date)（幂等 upsert）。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("CURRENT_TIMESTAMP")
MONEY = sa.Numeric(12, 2)


def upgrade() -> None:
    op.create_table(
        "shop_daily_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue", MONEY, nullable=False, server_default="0"),
        sa.Column("profit", MONEY, nullable=False, server_default="0"),
        sa.Column("ad_spend", MONEY, nullable=False, server_default="0"),
        sa.Column("items_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("profit_items_covered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_task_id", sa.BigInteger(), nullable=True),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=True),
        sa.Column("conversion_rate", sa.Numeric(6, 4), nullable=True),
        sa.UniqueConstraint("shop_id", "date", name="uq_shop_daily_shop_date"),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name="fk_shop_daily_shop"),
    )
    op.create_index("ix_shop_daily_date", "shop_daily_metrics", ["date"])

    op.create_table(
        "product_daily_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("units_sold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue", MONEY, nullable=False, server_default="0"),
        sa.Column("profit", MONEY, nullable=False, server_default="0"),
        sa.UniqueConstraint("product_id", "date", name="uq_product_daily_product_date"),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name="fk_product_daily_shop"),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="fk_product_daily_product"
        ),
    )
    op.create_index(
        "ix_product_daily_shop_date", "product_daily_metrics", ["shop_id", "date"]
    )

    op.create_table(
        "daily_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("daily_summary", sa.Text(), nullable=True),
        sa.Column("recommended_actions", sa.JSON(), nullable=True),
        sa.Column("sections_json", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("source_task_id", sa.BigInteger(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.UniqueConstraint("shop_id", "date", name="uq_daily_reports_shop_date"),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name="fk_daily_reports_shop"),
    )


def downgrade() -> None:
    op.drop_table("daily_reports")
    op.drop_index("ix_product_daily_shop_date", table_name="product_daily_metrics")
    op.drop_table("product_daily_metrics")
    op.drop_index("ix_shop_daily_date", table_name="shop_daily_metrics")
    op.drop_table("shop_daily_metrics")
