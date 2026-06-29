"""agent_tasks 服务：创建、状态机流转（条件门禁）、查询、回收。

所有 worker 状态写入都走条件 UPDATE，保证：
- 取消/终态任务不会被（重投的）消息再次执行；
- acks_late 下 worker 崩溃重投也不会重复执行。
"""
from __future__ import annotations

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent_task import AgentTask, TaskStatus


def create_task(
    db: Session,
    *,
    task_type: str,
    queue_name: str,
    idempotency_key: str,
    account_id: int | None = None,
    shop_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    input_json: dict | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    max_retries: int = 3,
) -> tuple[AgentTask, bool]:
    """创建 pending 任务记录。

    返回 (task, created)。若 idempotency_key 并发/已存在 → 捕获 IntegrityError 后
    re-read 已有记录返回，created=False（保证同一 key 只入队一次）。

    account_id：租户授权列（Phase 9）。建任务方应显式传入，使 shop 级与 account 级
    （如 shops 导入，shop_id 为空）任务都能经 get_for_account 按租户查询。
    """
    task = AgentTask(
        task_type=task_type,
        queue_name=queue_name,
        idempotency_key=idempotency_key,
        status=TaskStatus.PENDING,
        account_id=account_id,
        shop_id=shop_id,
        entity_type=entity_type,
        entity_id=entity_id,
        input_json=input_json,
        model=model,
        prompt_version=prompt_version,
        max_retries=max_retries,
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(AgentTask).where(AgentTask.idempotency_key == idempotency_key)
        ).scalar_one()
        return existing, False
    db.refresh(task)
    return task, True


def set_celery_id(db: Session, task_id: int, celery_task_id: str) -> None:
    db.execute(
        update(AgentTask)
        .where(AgentTask.id == task_id)
        .values(celery_task_id=celery_task_id)
    )
    db.commit()


def claim_running(db: Session, task_id: int) -> bool:
    """条件门禁：仅 pending/retry -> running，并写 started_at（租约起点）。

    返回 True 表示成功领取；False 表示已取消/终态/已被领取 → 调用方应跳过执行。
    """
    res = db.execute(
        update(AgentTask)
        .where(
            AgentTask.id == task_id,
            AgentTask.status.in_(TaskStatus.CLAIMABLE),
        )
        .values(status=TaskStatus.RUNNING, started_at=text("CURRENT_TIMESTAMP"))
    )
    db.commit()
    return res.rowcount > 0


def mark_succeeded(
    db: Session,
    task_id: int,
    *,
    output_json: dict | None,
    duration_ms: int,
    token_usage: int | None = None,
    cost=None,
) -> bool:
    """仅 running -> succeeded（worker 持有 running 时才可终结）。返回是否生效。"""
    res = db.execute(
        update(AgentTask)
        .where(AgentTask.id == task_id, AgentTask.status == TaskStatus.RUNNING)
        .values(
            status=TaskStatus.SUCCEEDED,
            output_json=output_json,
            duration_ms=duration_ms,
            token_usage=token_usage,
            cost=cost,
            finished_at=text("CURRENT_TIMESTAMP"),
        )
    )
    db.commit()
    return res.rowcount > 0


def finalize_succeeded(
    db: Session,
    task_id: int,
    *,
    output_json: dict | None,
    duration_ms: int,
    token_usage: int | None = None,
    cost=None,
    model: str | None = None,
    prompt_version: str | None = None,
    side_effect=None,
) -> bool:
    """原子地标记成功并执行业务副作用（如回写 reviews）—— 单事务、单提交。

    仅 running -> succeeded（条件门禁）。rowcount==0（已被 reaper/取消接管）则回滚、
    不执行副作用、返回 False。side_effect(db) 在同一事务内运行，提交前一并写入。
    """
    res = db.execute(
        update(AgentTask)
        .where(AgentTask.id == task_id, AgentTask.status == TaskStatus.RUNNING)
        .values(
            status=TaskStatus.SUCCEEDED,
            output_json=output_json,
            duration_ms=duration_ms,
            token_usage=token_usage,
            cost=cost,
            model=model,
            prompt_version=prompt_version,
            finished_at=text("CURRENT_TIMESTAMP"),
        )
    )
    if res.rowcount == 0:
        db.rollback()
        return False
    if side_effect is not None:
        side_effect(db)
    db.commit()
    return True


def mark_retry(
    db: Session,
    task_id: int,
    *,
    error_type: str,
    error_message: str,
    error_detail: str,
    from_statuses: tuple[str, ...] = (TaskStatus.RUNNING,),
) -> bool:
    """条件门禁：仅 from_statuses -> retry，并递增 retry_count。返回是否生效。"""
    res = db.execute(
        update(AgentTask)
        .where(AgentTask.id == task_id, AgentTask.status.in_(from_statuses))
        .values(
            status=TaskStatus.RETRY,
            retry_count=AgentTask.retry_count + 1,
            error_type=error_type,
            error_message=error_message,
            error_detail=error_detail,
        )
    )
    db.commit()
    return res.rowcount > 0


def mark_failed(
    db: Session,
    task_id: int,
    *,
    error_type: str,
    error_message: str,
    error_detail: str,
    from_statuses: tuple[str, ...] = (TaskStatus.RUNNING,),
) -> bool:
    """条件门禁：仅 from_statuses -> failed。返回是否生效。"""
    res = db.execute(
        update(AgentTask)
        .where(AgentTask.id == task_id, AgentTask.status.in_(from_statuses))
        .values(
            status=TaskStatus.FAILED,
            error_type=error_type,
            error_message=error_message,
            error_detail=error_detail,
            finished_at=text("CURRENT_TIMESTAMP"),
        )
    )
    db.commit()
    return res.rowcount > 0


def get_by_id(db: Session, task_id: int) -> AgentTask | None:
    return db.get(AgentTask, task_id)


def get_for_account(db: Session, account_id: int, task_id: int) -> AgentTask | None:
    """租户安全查询：仅返回 account_id 匹配的任务（Phase 9）。

    改用直接的 `account_id` 列授权（不再依赖 shop join）——使账号级任务（如 shops
    导入，shop_id 为空）也能被本租户查询；`account_id` 为 NULL 的系统任务不暴露。
    """
    return db.execute(
        select(AgentTask).where(
            AgentTask.id == task_id,
            AgentTask.account_id == account_id,
        )
    ).scalar_one_or_none()


def update_import_progress(db: Session, task_id: int, output_json: dict) -> bool:
    """导入任务进度/错误增量落库（Phase 9）：仅 running 时更新 output_json 并**提交本块事务**。

    调用约定（§1.3「数据 + 进度同事务」）：调用方用**同一 session** 先写本块业务数据
    （不 commit），再调本方法。本方法在同一事务内：
    - running 门禁命中（rowcount>0）→ `commit()`（业务数据 + 进度一起落库），返回 True；
    - 门禁失败（rowcount==0，任务已被 reaper/cancel/终态改走）→ `rollback()` 连同本块
      业务写入一并丢弃，返回 False（调用方据此停止后续处理，不再往非 running 任务落库）。
    只改 `output_json`，不碰 status/error_*——失败/重试后进度+错误报告仍可查。
    """
    res = db.execute(
        update(AgentTask)
        .where(AgentTask.id == task_id, AgentTask.status == TaskStatus.RUNNING)
        .values(output_json=output_json)
    )
    if res.rowcount == 0:
        db.rollback()
        return False
    db.commit()
    return True


def cancel_for_account(db: Session, account_id: int, task_id: int) -> AgentTask | None:
    """取消任务（仅 pending/retry 可取消）。返回最新记录；不存在/越权返回 None。"""
    task = get_for_account(db, account_id, task_id)
    if task is None:
        return None
    db.execute(
        update(AgentTask)
        .where(
            AgentTask.id == task_id,
            AgentTask.status.in_(TaskStatus.CANCELLABLE),
        )
        .values(status=TaskStatus.CANCELLED, finished_at=text("CURRENT_TIMESTAMP"))
    )
    db.commit()
    return db.get(AgentTask, task_id)


def reap_orphans(
    db: Session, *, pending_timeout_s: int = 300, running_timeout_s: int = 660
) -> dict:
    """回收孤儿任务（**每条原子条件回收**，并发 reaper 安全）：

    - pending 且无 celery_task_id 且超时 → 视为入队失败的孤儿；
    - running 且 started_at 超时 → 视为 worker 崩溃卡死。
    每条按 retry_count/max_retries 用带超时谓词的条件 UPDATE 改为 retry 或 failed，
    仅影响 1 行才计数/重入队（避免重复递增与重复入队）。重入队由调用方负责。
    返回 {"counts": {...}, "requeue": [...]}。
    """
    # 超时阈值为内部 int 参数（非用户输入），int 内联，安全且省去绑定歧义。
    pending_cond = text(
        f"created_at < (NOW() - INTERVAL {int(pending_timeout_s)} SECOND)"
    )
    running_cond = text(
        f"started_at < (NOW() - INTERVAL {int(running_timeout_s)} SECOND)"
    )

    # 候选（读取用于决定 retry/fail；真正的回收用带状态+超时谓词的条件 UPDATE 保证原子）
    candidates: list[tuple[AgentTask, str, object]] = []
    for task in db.execute(
        select(AgentTask).where(
            AgentTask.status == TaskStatus.PENDING,
            AgentTask.celery_task_id.is_(None),
            pending_cond,
        )
    ).scalars().all():
        candidates.append((task, TaskStatus.PENDING, pending_cond))
    for task in db.execute(
        select(AgentTask).where(
            AgentTask.status == TaskStatus.RUNNING,
            running_cond,
        )
    ).scalars().all():
        candidates.append((task, TaskStatus.RUNNING, running_cond))

    counts = {"retried": 0, "failed": 0}
    requeue: list[dict] = []
    for task, from_status, timeout_cond in candidates:
        if task.retry_count < task.max_retries:
            res = db.execute(
                update(AgentTask)
                .where(
                    AgentTask.id == task.id,
                    AgentTask.status == from_status,
                    timeout_cond,
                )
                .values(
                    status=TaskStatus.RETRY,
                    retry_count=AgentTask.retry_count + 1,
                    error_type="OrphanReclaim",
                    error_message=f"reclaimed stuck {from_status} task",
                    error_detail="",
                )
            )
            db.commit()
            if res.rowcount > 0:
                requeue.append({"id": task.id, "task_type": task.task_type})
                counts["retried"] += 1
        else:
            res = db.execute(
                update(AgentTask)
                .where(
                    AgentTask.id == task.id,
                    AgentTask.status == from_status,
                    timeout_cond,
                )
                .values(
                    status=TaskStatus.FAILED,
                    error_type="OrphanReclaim",
                    error_message=f"reclaimed {from_status} task exceeded max_retries",
                    error_detail="",
                    finished_at=text("CURRENT_TIMESTAMP"),
                )
            )
            db.commit()
            if res.rowcount > 0:
                counts["failed"] += 1
    return {"counts": counts, "requeue": requeue}
