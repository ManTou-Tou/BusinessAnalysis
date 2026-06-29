"""Agent 任务路由：状态查询、取消，以及 dev 用的示范任务触发与回收。

生命周期所有权（PHASE_3_PLAN.md §2a）：API 先建 pending 记录 → 入队 → 回填
celery_task_id → 返回业务任务 id。入队失败则把记录标 failed，调用方可见。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_account_id, require_dev_env
from app.core.db import get_db
from app.core.ratelimit import RateLimit
from app.schemas.agent_task import AgentTaskOut, SampleTaskCreate
from app.services import agent_task as svc
from app.services.tenant import get_owned_shop

router = APIRouter(prefix="/agent/tasks", tags=["agent-tasks"])


@router.get("/{task_id}", response_model=AgentTaskOut)
def get_task(
    task_id: int,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> AgentTaskOut:
    task = svc.get_for_account(db, account_id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="task not found"
        )
    return task


@router.post(
    "/{task_id}/cancel",
    response_model=AgentTaskOut,
    dependencies=[Depends(RateLimit("agent_tasks.cancel"))],
)
def cancel_task(
    task_id: int,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> AgentTaskOut:
    task = svc.cancel_for_account(db, account_id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="task not found"
        )
    return task


@router.post(
    "/sample",
    response_model=AgentTaskOut,
    status_code=201,
    dependencies=[Depends(require_dev_env)],
)
def trigger_sample(
    payload: SampleTaskCreate,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> AgentTaskOut:
    """dev 用：创建并入队一个示范任务，验证整条链路。"""
    from app.tasks.sample import noop_task

    get_owned_shop(db, account_id, payload.shop_id)  # 校验店铺归属
    idem = payload.idempotency_key or f"sample:{uuid.uuid4().hex}"

    task, created = svc.create_task(
        db,
        task_type="sample.noop",
        queue_name="default",
        idempotency_key=idem,
        account_id=account_id,
        shop_id=payload.shop_id,
        input_json=payload.payload,
    )
    # 已存在（幂等命中）则不重复入队，直接返回现状
    if not created:
        return task

    try:
        async_res = noop_task.apply_async(
            args=[task.id], kwargs={"payload": payload.payload}
        )
        svc.set_celery_id(db, task.id, async_res.id)
    except Exception as exc:  # noqa: BLE001 - 入队失败也要留可见记录，不抛 500 黑洞
        # 此时任务仍为 pending（尚未被 worker 领取），从 pending 置 failed
        from app.models.agent_task import TaskStatus

        svc.mark_failed(
            db,
            task.id,
            error_type="EnqueueError",
            error_message=str(exc),
            error_detail="",
            from_statuses=(TaskStatus.PENDING,),
        )
    return svc.get_by_id(db, task.id)


@router.post(
    "/maintenance/reap",
    dependencies=[Depends(require_dev_env)],
)
def trigger_reap(db: Session = Depends(get_db)) -> dict:
    """dev 用：手动触发孤儿/卡死任务回收并重新入队（同步执行，便于测试观察）。"""
    from app.tasks.maintenance import run_reap

    return run_reap(db)
