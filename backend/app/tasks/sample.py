"""示范任务（不含 LLM）：打通「入队 → 门禁 → 执行 → 落库」链路并验证可观测性。

payload 支持 {"fail": true} 触发失败/重试路径用于测试。
Celery 任务的 max_retries 设大，实际重试上限由 agent_tasks.max_retries（DB）管控。
"""
from __future__ import annotations

from app.core.celery_app import celery_app
from app.tasks.base import run_lifecycle


def _noop_logic(payload: dict | None) -> dict:
    if payload and payload.get("fail"):
        raise ValueError("sample task forced failure (payload.fail=true)")
    return {"ok": True, "echo": payload or {}}


@celery_app.task(bind=True, name="app.tasks.sample.noop", max_retries=100)
def noop_task(self, agent_task_id: int, payload: dict | None = None):
    return run_lifecycle(self, agent_task_id, _noop_logic, payload)
