"""shops: unique(account_id, external_shop_id) for Excel upsert key

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-25

Phase 9（Excel 导入）：shops 用 (account_id, external_shop_id) 作 upsert 稳定键。
加唯一约束前**先检测存量非 NULL 重复**，有则主动报错要求清理（不静默建失败）。
导入模板要求 external_shop_id 必填非空（空值整行报错）；MySQL 唯一索引允许多 NULL，
但 upsert 依赖非空键，故空值不参与。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    dupes = conn.execute(
        sa.text(
            "SELECT account_id, external_shop_id, COUNT(*) AS c FROM shops "
            "WHERE external_shop_id IS NOT NULL "
            "GROUP BY account_id, external_shop_id HAVING c > 1"
        )
    ).fetchall()
    if dupes:
        sample = ", ".join(
            f"(account_id={r[0]}, external_shop_id={r[1]!r}, count={r[2]})" for r in dupes[:10]
        )
        raise RuntimeError(
            "cannot add unique(account_id, external_shop_id) on shops: "
            f"{len(dupes)} duplicate key group(s) exist; clean up存量后再迁移. Examples: {sample}"
        )
    op.create_unique_constraint(
        "uq_shops_account_external", "shops", ["account_id", "external_shop_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_shops_account_external", "shops", type_="unique")
