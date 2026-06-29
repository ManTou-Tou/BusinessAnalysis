"""initial schema: accounts, shops, products, orders, order_items, reviews

Revision ID: 0001
Revises:
Create Date: 2026-06-19

Phase 2 干净基线：金额 DECIMAL(12,2)、多租户(account->shop)、
uniq(shop_id, order_no)、order_items 快照字段、keyset 友好索引。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=NOW),
    )

    op.create_table(
        "shops",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("shop_name", sa.String(255), nullable=False),
        sa.Column("external_shop_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
    )
    op.create_index("ix_shops_account_id", "shops", ["account_id"])

    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("sku", sa.String(128), nullable=False),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"]),
        sa.UniqueConstraint("shop_id", "sku", name="uq_products_shop_sku"),
    )
    op.create_index("ix_products_shop_status", "products", ["shop_id", "status"])

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("order_no", sa.String(128), nullable=False),
        sa.Column("order_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("shipping_fee", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("platform_fee", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("ad_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"]),
        sa.UniqueConstraint("shop_id", "order_no", name="uq_orders_shop_order_no"),
    )
    op.create_index("ix_orders_shop_created", "orders", ["shop_id", "created_at"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        sa.Column("external_product_id", sa.String(128), nullable=True),
        sa.Column("sku_snapshot", sa.String(128), nullable=True),
        sa.Column("product_name_snapshot", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sale_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("profit", sa.Numeric(12, 2), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
    )
    op.create_index("ix_order_items_order", "order_items", ["order_id"])
    op.create_index("ix_order_items_product", "order_items", ["product_id"])

    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("buyer_name", sa.String(255), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("review_text", sa.Text(), nullable=False),
        sa.Column("review_type", sa.String(32), nullable=True),
        sa.Column("sentiment", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
    )
    op.create_index("ix_reviews_product_created", "reviews", ["product_id", "created_at"])
    op.create_index("ix_reviews_shop_type", "reviews", ["shop_id", "review_type"])
    op.create_index("ix_reviews_sentiment", "reviews", ["sentiment"])


def downgrade() -> None:
    op.drop_table("reviews")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("products")
    op.drop_table("shops")
    op.drop_table("accounts")
