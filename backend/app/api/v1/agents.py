"""Agent 触发路由（业务语义入口）。

POST /agent/review-classifier：校验评论归属当前租户 → 建 agent_task（llm 队列）
→ 入队 → 返回业务任务 id。状态查询走 /agent/tasks/{id}。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.review_classifier import ReviewClassifierAgent
from app.api.deps import get_account_id
from app.core.config import settings
from app.core.db import get_db
from app.core.ratelimit import RateLimit
from app.models.agent_task import TaskStatus
from app.models.order import Order
from app.models.review import Review
from app.models.shop import Shop
from app.schemas.agent_task import AgentTaskOut, ReviewClassifyRequest
from app.schemas.profit import ProfitAnalysisRequest
from app.schemas.report import DailyReportRequest
from app.services import agent_task as svc
from app.services.tenant import get_owned_shop

router = APIRouter(prefix="/agent", tags=["agents"])


def _get_owned_review(db: Session, account_id: int, review_id: int) -> Review:
    review = db.execute(
        select(Review)
        .join(Shop, Review.shop_id == Shop.id)
        .where(Review.id == review_id, Shop.account_id == account_id)
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="review not found"
        )
    return review


@router.post(
    "/review-classifier",
    response_model=AgentTaskOut,
    status_code=201,
    dependencies=[
        Depends(RateLimit("agent.review_classifier", limit=settings.ratelimit_llm_trigger_limit))
    ],
)
def classify_review(
    payload: ReviewClassifyRequest,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> AgentTaskOut:
    review = _get_owned_review(db, account_id, payload.review_id)

    base_key = f"review.classify:{review.id}:{ReviewClassifierAgent.prompt_version}"
    idem = base_key if not payload.force else f"{base_key}:{uuid.uuid4().hex}"

    task, created = svc.create_task(
        db,
        task_type="review.classify",
        queue_name="llm",
        idempotency_key=idem,
        account_id=account_id,
        shop_id=review.shop_id,
        entity_type="review",
        entity_id=review.id,
        input_json={"review_id": review.id},
    )
    if not created:
        return task  # 幂等命中：返回已有任务，不重复入队

    try:
        from app.tasks.llm import classify_review_task

        async_res = classify_review_task.apply_async(args=[task.id])
        svc.set_celery_id(db, task.id, async_res.id)
    except Exception as exc:  # noqa: BLE001 - 入队失败留可见记录，不抛 500
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
    "/profit-analysis",
    response_model=AgentTaskOut,
    status_code=201,
    dependencies=[
        Depends(RateLimit("agent.profit_analysis", limit=settings.ratelimit_llm_trigger_limit))
    ],
)
def profit_analysis(
    payload: ProfitAnalysisRequest,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> AgentTaskOut:
    """触发确定性利润计算（异步，queue=default）。状态查 /agent/tasks/{id}。"""
    get_owned_shop(db, account_id, payload.shop_id)

    if payload.order_id is not None:
        owned = db.execute(
            select(Order.id).where(
                Order.id == payload.order_id, Order.shop_id == payload.shop_id
            )
        ).scalar_one_or_none()
        if owned is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            )
    else:
        # 全店 scope：不静默截断——超过单次上限即拒绝（避免半量成功后被幂等键挡住、
        # 剩余订单永不计算）。客户端可改用 order_id 逐单触发，或调大上限/后续 dispatcher。
        total = db.execute(
            select(func.count()).select_from(Order).where(Order.shop_id == payload.shop_id)
        ).scalar_one()
        if total > settings.profit_max_orders_per_run:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"shop has {total} orders exceeding PROFIT_MAX_ORDERS_PER_RUN="
                    f"{settings.profit_max_orders_per_run}; narrow scope (order_id) "
                    "or raise the limit"
                ),
            )

    scope = str(payload.order_id) if payload.order_id is not None else "all"
    base_key = f"profit.compute:{payload.shop_id}:{scope}:{settings.profit_calc_version}"
    idem = base_key if not payload.force else f"{base_key}:{uuid.uuid4().hex}"

    task, created = svc.create_task(
        db,
        task_type="profit.compute",
        queue_name="default",
        idempotency_key=idem,
        account_id=account_id,
        shop_id=payload.shop_id,
        entity_type="order" if payload.order_id is not None else "shop",
        entity_id=payload.order_id,
        input_json={"order_id": payload.order_id},
    )
    if not created:
        return task  # 幂等命中：返回已有任务，不重复入队

    try:
        from app.tasks.profit import compute_profit_task

        async_res = compute_profit_task.apply_async(args=[task.id])
        svc.set_celery_id(db, task.id, async_res.id)
    except Exception as exc:  # noqa: BLE001 - 入队失败留可见记录，不抛 500
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
    "/daily-report",
    response_model=AgentTaskOut,
    status_code=201,
    dependencies=[
        Depends(RateLimit("agent.daily_report", limit=settings.ratelimit_llm_trigger_limit))
    ],
)
def daily_report(
    payload: DailyReportRequest,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> AgentTaskOut:
    """触发日报生成（确定性聚合 + LLM 总结，异步 llm 队列）。状态查 /agent/tasks/{id}。"""
    get_owned_shop(db, account_id, payload.shop_id)
    d = payload.date or (datetime.now(timezone.utc).date() - timedelta(days=1))

    base_key = f"daily.report:{payload.shop_id}:{d.isoformat()}:{settings.report_calc_version}"
    idem = base_key if not payload.force else f"{base_key}:{uuid.uuid4().hex}"

    task, created = svc.create_task(
        db,
        task_type="daily.report",
        queue_name="llm",
        idempotency_key=idem,
        account_id=account_id,
        shop_id=payload.shop_id,
        entity_type="shop",
        input_json={"date": d.isoformat()},
    )
    if not created:
        return task  # 幂等命中：返回已有任务，不重复入队

    try:
        from app.tasks.report import generate_daily_report_task

        async_res = generate_daily_report_task.apply_async(args=[task.id])
        svc.set_celery_id(db, task.id, async_res.id)
    except Exception as exc:  # noqa: BLE001 - 入队失败留可见记录，不抛 500
        svc.mark_failed(
            db,
            task.id,
            error_type="EnqueueError",
            error_message=str(exc),
            error_detail="",
            from_statuses=(TaskStatus.PENDING,),
        )
    return svc.get_by_id(db, task.id)
