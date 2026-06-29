"""high concurrency: covering/sort indexes for hot query paths

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-25

Phase 8（高并发优化）索引体检：仅 `create_index`，不改表结构、不删旧索引。
每个索引绑定一条真实热查询（附 service 源码位置）。详见 PHASE_8_PLAN.md §1.2.A。

设计要点：keyset 列表/批量分类把 `id` 放索引尾列——等值前缀过滤后 `id` 即索引序，
消除 `ORDER BY id` 的 filesort；日报聚合按 `(shop_id, created_at)` / `(shop_id,
sentiment, created_at)` 服务范围 + 分组；reaper 按 `(status, created_at)` /
`(status, started_at)` 服务超时扫描（1M tasks/day 下避免全 status 扫描）。
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- reviews ---
    # keyset 列表（带 shop_id，不带 product_id）：WHERE shop_id=? ORDER BY id DESC
    # （review.list_reviews）。必须保留，(product_id,id) 不含 shop_id 前缀、不能替代。
    op.create_index("ix_reviews_shop_id", "reviews", ["shop_id", "id"])
    # keyset 列表（带 product_id，无论是否带 shop_id）：WHERE product_id=? [AND shop_id=?]
    # ORDER BY id DESC（review.list_reviews；API 允许 product_id 单独过滤）。
    # product_id 高选择度，shop_id 作残余过滤近乎免费 → 同时覆盖两条路径。
    op.create_index("ix_reviews_product_id", "reviews", ["product_id", "id"])
    # 批量分类选取：WHERE shop_id=? AND review_type IS NULL ORDER BY id LIMIT n
    # （review.enqueue_classification）。最高价值：NULL 等值过滤 + 有序 limit。
    op.create_index(
        "ix_reviews_shop_type_id", "reviews", ["shop_id", "review_type", "id"]
    )
    # 日报评论计数：WHERE shop_id=? AND created_at∈[d,d+1)（report.aggregate 总数/负面计数）。
    op.create_index("ix_reviews_shop_created", "reviews", ["shop_id", "created_at"])
    # 日报负面分组：WHERE shop_id=? AND sentiment='negative' AND created_at∈[d,d+1)
    # GROUP BY review_type（report.aggregate）。负面抽样的 ORDER BY id LIMIT 接受小
    # filesort（当天负面集小、limit≤20），不为其单建索引（见 PHASE_8_PLAN §1.2.A）。
    op.create_index(
        "ix_reviews_shop_sentiment_created",
        "reviews",
        ["shop_id", "sentiment", "created_at"],
    )

    # --- products ---
    # keyset 列表（带 shop_id）：WHERE shop_id=? ORDER BY id DESC（product.list_products）。
    op.create_index("ix_products_shop_id", "products", ["shop_id", "id"])

    # --- orders ---
    # keyset 列表（带 shop_id）：WHERE shop_id=? ORDER BY id DESC（order.list_orders）。
    # 注意：现有 (shop_id, created_at) 不能服务 ORDER BY id（MySQL 不因 id/created_at
    # 相关性等价替换），故必须独立加 (shop_id, id)。
    op.create_index("ix_orders_shop_id", "orders", ["shop_id", "id"])

    # --- agent_tasks（reaper 孤儿/卡死扫描） ---
    # 扫 pending 孤儿：WHERE status='pending' AND celery_task_id IS NULL AND created_at<阈值
    # （agent_task.reap）。
    op.create_index("ix_agent_tasks_status_created", "agent_tasks", ["status", "created_at"])
    # 扫 running 卡死：WHERE status='running' AND started_at<阈值（agent_task.reap）。
    op.create_index("ix_agent_tasks_status_started", "agent_tasks", ["status", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_tasks_status_started", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_status_created", table_name="agent_tasks")
    op.drop_index("ix_orders_shop_id", table_name="orders")
    op.drop_index("ix_products_shop_id", table_name="products")
    op.drop_index("ix_reviews_shop_sentiment_created", table_name="reviews")
    op.drop_index("ix_reviews_shop_created", table_name="reviews")
    op.drop_index("ix_reviews_shop_type_id", table_name="reviews")
    op.drop_index("ix_reviews_product_id", table_name="reviews")
    op.drop_index("ix_reviews_shop_id", table_name="reviews")
