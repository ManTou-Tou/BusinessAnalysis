from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class SampleTaskCreate(BaseModel):
    """触发示范任务（dev 用）。"""

    shop_id: int
    idempotency_key: str | None = None  # 不传则自动生成
    payload: dict[str, Any] | None = None


class ReviewClassifyRequest(BaseModel):
    """触发单条评论分类。"""

    review_id: int
    force: bool = False  # 强制重新分类（绕过幂等键，用于 prompt 升级后重跑）


class AgentTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    idempotency_key: str
    celery_task_id: str | None
    task_type: str
    queue_name: str
    status: str
    shop_id: int | None
    entity_type: str | None
    entity_id: int | None
    retry_count: int
    max_retries: int
    input_json: dict[str, Any] | None
    output_json: dict[str, Any] | None
    error_type: str | None
    error_message: str | None
    # error_detail（traceback）不对外暴露，仅内部排障
    model: str | None
    prompt_version: str | None
    token_usage: int | None
    cost: Decimal | None
    duration_ms: int | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
