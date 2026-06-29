"""任务生命周期框架。

业务任务只写核心逻辑（logic_fn），生命周期（领取门禁、计时、落库、重试/失败）
统一由 run_lifecycle 处理，保证所有任务的可观测字段一致。

所有权边界（见 PHASE_3_PLAN.md §2a）：
- API 先建 pending 记录并入队，worker 只更新该记录；
- 进入执行用条件门禁（仅 pending/retry -> running），影响 0 行则跳过（取消/终态/重投）。
"""
from __future__ import annotations

import time
import traceback
from collections.abc import Callable

from app.core.db import SessionLocal
from app.services import agent_task as svc

_MAX_BACKOFF_S = 600
_ERROR_DETAIL_MAX = 2000


def run_lifecycle(
    task,
    agent_task_id: int,
    logic_fn: Callable[[dict | None], object],
    payload: dict | None,
    *,
    success_handler: Callable[[object, int, object, int], None] | None = None,
):
    """在生命周期框架内执行 logic_fn。

    task: 绑定的 Celery 任务实例（bind=True），用于 task.retry。
    success_handler(db, agent_task_id, result, duration_ms)：可选。提供时由它**原子地**
    标记成功并写业务副作用（见 svc.finalize_succeeded）；不提供则默认 mark_succeeded(result)。
    返回 logic_fn 的结果或 None（被跳过/终态失败）。
    """
    db = SessionLocal()
    try:
        # 条件门禁：领取失败说明已取消/终态/被其他 worker 领取 → 跳过执行
        if not svc.claim_running(db, agent_task_id):
            return None

        t0 = time.perf_counter()
        # 同一 try 包住「执行 + 落库成功」：success_handler/回写/commit 抛错也走失败处理，
        # 不会把任务卡在 running。异常时先 rollback，丢弃未提交的 succeeded 更新，
        # 让 _handle_failure 在干净状态（DB 仍为 running）上正确转 retry/failed。
        try:
            result = logic_fn(payload)
            duration_ms = int((time.perf_counter() - t0) * 1000)
            if success_handler is not None:
                success_handler(db, agent_task_id, result, duration_ms)
            else:
                svc.mark_succeeded(
                    db, agent_task_id, output_json=result, duration_ms=duration_ms
                )
        except Exception as exc:  # noqa: BLE001 - 统一兜底，落库后按策略重试/失败
            db.rollback()
            return _handle_failure(task, db, agent_task_id, exc)

        return result
    finally:
        db.close()


def _handle_failure(task, db, agent_task_id: int, exc: Exception):
    record = svc.get_by_id(db, agent_task_id)
    error_type = type(exc).__name__
    error_detail = traceback.format_exc()[:_ERROR_DETAIL_MAX]

    # 达上限则终态 failed；否则置 retry（递增 retry_count）并由 Celery 退避重投。
    # mark_* 带 running 条件门禁：若该记录已被 reaper 等改走，则不重复处理/重投。
    if record is None or record.retry_count >= record.max_retries:
        svc.mark_failed(
            db,
            agent_task_id,
            error_type=error_type,
            error_message=str(exc),
            error_detail=error_detail,
        )
        return None

    retried = svc.mark_retry(
        db,
        agent_task_id,
        error_type=error_type,
        error_message=str(exc),
        error_detail=error_detail,
    )
    if not retried:
        # running 状态已被他方接管（如 reaper 已重置/重入队）→ 不再重复入队
        return None
    countdown = min(2 ** record.retry_count, _MAX_BACKOFF_S)
    # 抛出 Retry 由 Celery 重投；下次执行 claim_running 会把 retry -> running
    raise task.retry(exc=exc, countdown=countdown)
