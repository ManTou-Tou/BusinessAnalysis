"""FastAPI 应用入口（Phase 1）。

提供分级健康检查：
- /health  存活探针（不依赖外部）
- /ready   就绪探针（检查 MySQL + Redis 可连）
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Response, status

from app.api.v1 import api_router
from app.core.config import settings
from app.core.db import check_db
from app.core.redis import check_redis

logger = logging.getLogger(__name__)

app = FastAPI(title="AI E-Commerce Copilot", version="0.1.0")
app.include_router(api_router)


@app.get("/health")
def health() -> dict[str, str]:
    """存活探针：进程在跑即返回 200。"""
    return {"status": "ok", "env": settings.app_env}


@app.get("/ready")
def ready(response: Response) -> dict[str, object]:
    """就绪探针：检查 DB 与 Redis 连通性，任一失败返回 503。"""
    checks: dict[str, str] = {}

    try:
        check_db()
        checks["mysql"] = "ok"
    except Exception:  # noqa: BLE001 - 探针需汇报任意失败原因
        logger.exception("readiness check failed: mysql")
        checks["mysql"] = "error"

    try:
        check_redis()
        checks["redis"] = "ok"
    except Exception:  # noqa: BLE001
        logger.exception("readiness check failed: redis")
        checks["redis"] = "error"

    ready_ok = all(v == "ok" for v in checks.values())
    if not ready_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready_ok, "checks": checks}
