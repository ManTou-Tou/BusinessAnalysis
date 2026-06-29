"""agent_tasks: 异步任务状态/审计表

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-19

落实 Phase 2 遗留固化项：idempotency_key 唯一约束、status 枚举（应用层约束）、
审计字段齐全。弱关联业务实体（shop_id 可空，无外键）。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("queue_name", sa.String(32), nullable=False, server_default="default"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("shop_id", sa.BigInteger(), nullable=True),
        sa.Column("entity_type", sa.String(32), nullable=True),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("token_usage", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=NOW),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_tasks_idempotency_key"),
        sa.ForeignKeyConstraint(
            ["shop_id"], ["shops.id"], name="fk_agent_tasks_shop", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_agent_tasks_status_type", "agent_tasks", ["status", "task_type"])
    op.create_index("ix_agent_tasks_shop_created", "agent_tasks", ["shop_id", "created_at"])
    op.create_index("ix_agent_tasks_celery", "agent_tasks", ["celery_task_id"])


def downgrade() -> None:
    op.drop_table("agent_tasks")
