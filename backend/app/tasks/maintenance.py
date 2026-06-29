"""维护任务：回收孤儿/卡死任务（MVP 手动触发；Beat 定时放后续）。

reap 把超时的 pending（入队失败）与 running（worker 崩溃卡死）按 retry_count/
max_retries 原子重置为 retry 或 failed，并对重置为 retry 的任务按 task_type 重新入队。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.db import SessionLocal
from app.models.agent_task import TaskStatus
from app.services import agent_task as svc


# task_type -> 重入队用的 Celery 任务（惰性 import 避免环）
def _registry():
    from app.tasks.imports import run_import_task
    from app.tasks.llm import classify_review_task, classify_reviews_batch_task
    from app.tasks.profit import compute_profit_task
    from app.tasks.report import generate_daily_report_task
    from app.tasks.sample import noop_task

    return {
        "sample.noop": noop_task,
        "review.classify": classify_review_task,
        "review.classify_batch": classify_reviews_batch_task,
        "profit.compute": compute_profit_task,
        "daily.report": generate_daily_report_task,
        # Phase 9：Excel 导入（按实体的 task_type，孤儿可重投——reviews 续跑、其它重跑）。
        "import.shops": run_import_task,
        "import.products": run_import_task,
        "import.orders": run_import_task,
        "import.reviews": run_import_task,
    }


def run_reap(
    db: Session, *, pending_timeout_s: int = 300, running_timeout_s: int = 660
) -> dict:
    """回收孤儿任务并对重置为 retry 的任务重新入队。同步函数，供 Celery 任务与 dev 端点共用。"""
    result = svc.reap_orphans(
        db, pending_timeout_s=pending_timeout_s, running_timeout_s=running_timeout_s
    )
    registry = _registry()
    requeued = 0
    requeue_failed = 0
    for item in result["requeue"]:
        # 这些任务已被 reap_orphans 置为 retry。若重入队失败，必须显式置 failed，
        # 否则会永久卡在 retry（reaper 只扫 pending/running，不会再捞 retry）。
        celery_task = registry.get(item["task_type"])
        if celery_task is None:
            if svc.mark_failed(
                db,
                item["id"],
                error_type="UnknownTaskType",
                error_message=f"no registered task for type {item['task_type']!r}",
                error_detail="",
                from_statuses=(TaskStatus.RETRY,),
            ):
                requeue_failed += 1
            continue
        record = svc.get_by_id(db, item["id"])
        try:
            async_res = celery_task.apply_async(
                args=[item["id"]], kwargs={"payload": record.input_json}
            )
            svc.set_celery_id(db, item["id"], async_res.id)
            requeued += 1
        except Exception as exc:  # noqa: BLE001 - 重投失败则置 failed，避免卡死 retry
            db.rollback()  # set_celery_id 可能在 commit 时失败，先回滚再写 failed
            if svc.mark_failed(
                db,
                item["id"],
                error_type="RequeueError",
                error_message=str(exc),
                error_detail="",
                from_statuses=(TaskStatus.RETRY,),
            ):
                requeue_failed += 1
    return {
        "counts": result["counts"],
        "requeued": requeued,
        "requeue_failed": requeue_failed,
    }


@celery_app.task(name="app.tasks.maintenance.reap")
def reap_task(pending_timeout_s: int = 300, running_timeout_s: int = 660) -> dict:
    db = SessionLocal()
    try:
        return run_reap(
            db, pending_timeout_s=pending_timeout_s, running_timeout_s=running_timeout_s
        )
    finally:
        db.close()
