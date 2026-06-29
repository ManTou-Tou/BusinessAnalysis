"""agent_tasks: add account_id (tenant authz for account-level tasks)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-25

Phase 9：shops 导入任务 shop_id=NULL，无法经 shop->account join 授权查询。
给 agent_tasks 加 account_id（直接租户列），回填存量（有 shop_id 的从 shop 取），
授权改按 account_id（覆盖 shop 级与 account 级任务）。无 shop_id 的系统任务保持 NULL、
不对用户暴露（与既有行为一致）。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("account_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_agent_tasks_account_created", "agent_tasks", ["account_id", "created_at"]
    )
    # 回填存量：有 shop_id 的任务从其 shop 取 account_id（系统任务 shop_id 为 NULL → 保持 NULL）。
    op.execute(
        "UPDATE agent_tasks t JOIN shops s ON t.shop_id = s.id "
        "SET t.account_id = s.account_id"
    )


def downgrade() -> None:
    op.drop_index("ix_agent_tasks_account_created", table_name="agent_tasks")
    op.drop_column("agent_tasks", "account_id")
