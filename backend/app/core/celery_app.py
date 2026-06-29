"""Celery 应用与配置（Phase 3）。

启动 worker：
    celery -A app.core.celery_app:celery_app worker -Q default,llm,sync --loglevel=info

队列隔离：default（轻任务）/ llm（Agent/LLM）/ sync（数据导入）。
任务最终状态以 MySQL(agent_tasks) 为准，Redis 仅作 broker + 短期 result。
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "copilot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.sample",
        "app.tasks.maintenance",
        "app.tasks.llm",
        "app.tasks.profit",
        "app.tasks.report",
        "app.tasks.imports",
    ],
)

celery_app.conf.update(
    # 序列化
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 时区
    enable_utc=True,
    # 结果只短期留 Redis（真相在 MySQL）
    result_expires=3600,
    # 队列路由（按任务名前缀分队列）
    task_default_queue="default",
    task_routes={
        "app.tasks.sample.*": {"queue": "default"},
        "app.tasks.maintenance.*": {"queue": "default"},
        "app.tasks.profit.*": {"queue": "default"},
        # Phase 7：按**具体任务名**路由——生成（含 LLM）走 llm，派发（纯 fan-out）走 default。
        # 注意顺序：精确名匹配优先于下面 llm.* 的通配，故不要用 app.tasks.report.* 一刀切。
        "app.tasks.report.generate_daily_report": {"queue": "llm"},
        "app.tasks.report.dispatch_daily_reports": {"queue": "default"},
        "app.tasks.llm.*": {"queue": "llm"},
        # Phase 9：Excel 导入（解析+入库，I/O 重）走 sync 队列。
        "app.tasks.imports.*": {"queue": "sync"},
        "app.tasks.sync.*": {"queue": "sync"},
    },
    # Beat：每日 02:00 UTC 派发昨日日报（MVP 单实例；多实例锁延后）。
    beat_schedule={
        "dispatch-daily-reports": {
            "task": "app.tasks.report.dispatch_daily_reports",
            "schedule": crontab(hour=2, minute=0),
        },
    },
    # 超时：软超时先于硬超时抛异常，走失败/重试
    task_time_limit=600,
    task_soft_time_limit=540,
    # 可靠投递：执行完才 ack；慢任务场景禁用预取饿死；worker 异常丢失则 requeue
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    # 任务开始即上报（便于观测）
    task_track_started=True,
)
