"""异步任务记录：agent_tasks（任务状态/审计真相，落 MySQL）。

弱关联业务实体（shop_id + entity_type/entity_id，无外键），任务记录属审计日志，
不随业务数据级联删除。状态机见 TaskStatus。
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY = "retry"
    CANCELLED = "cancelled"

    # 可被 worker 领取执行的状态（条件门禁）
    CLAIMABLE = (PENDING, RETRY)
    # 可被取消的状态
    CANCELLABLE = (PENDING, RETRY)
    # 终态，不再自动流转
    TERMINAL = (SUCCEEDED, FAILED, CANCELLED)


class AgentTask(TimestampMixin, Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("ix_agent_tasks_status_type", "status", "task_type"),
        Index("ix_agent_tasks_shop_created", "shop_id", "created_at"),
        Index("ix_agent_tasks_celery", "celery_task_id"),
        # Phase 8（迁移 0004）：reaper 超时扫描（pending 用 created_at、running 用 started_at）。
        Index("ix_agent_tasks_status_created", "status", "created_at"),
        Index("ix_agent_tasks_status_started", "status", "started_at"),
        # Phase 9（迁移 0006）：账号级任务（如 shops 导入，shop_id 为空）的租户授权列。
        Index("ix_agent_tasks_account_created", "account_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 幂等键：唯一约束，同一逻辑任务只入队一次
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TaskStatus.PENDING
    )

    # account_id 可空：租户授权列（Phase 9）。账号级任务（如 shops 导入，shop_id 为空）
    # 也能按 account 授权查询；系统任务保持 NULL、不对用户暴露。建任务时由 API 显式填入。
    account_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # shop_id 可空：支持非店铺级任务；非空时按租户校验/查询。
    # FK ON DELETE SET NULL：店铺删除时保留审计记录（置空），不级联删任务。
    shop_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("shops.id", ondelete="SET NULL"), nullable=True
    )
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # traceback 摘要

    # LLM / 成本（sample 任务为空；Phase 5 起填充）
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    token_usage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)  # USD，可含亚分

    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
