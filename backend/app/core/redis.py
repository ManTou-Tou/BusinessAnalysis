"""Redis 连接（Phase 1 仅作 Celery broker/backend 与连通性检查）。"""
from __future__ import annotations

import redis

from app.core.config import settings

redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def check_redis() -> bool:
    """连通性检查，用于 /ready。"""
    return bool(redis_client.ping())
