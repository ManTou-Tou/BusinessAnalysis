"""Review 服务（批量导入 + 列表 + 批量分类分发，经 shop 归属到 account）。"""
import hashlib
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent_task import TaskStatus
from app.models.order import Order
from app.models.product import Product
from app.models.review import Review
from app.models.shop import Shop
from app.schemas.review import ReviewClassifyBatchRequest, ReviewImportRequest
from app.services import agent_task as task_svc
from app.services.tenant import get_owned_shop
from app.utils.pagination import keyset_paginate


def import_reviews(db: Session, account_id: int, payload: ReviewImportRequest) -> int:
    get_owned_shop(db, account_id, payload.shop_id)

    # 单次导入上限（Phase 8）：超出显式 400 提示分批，不静默截断、也限定单事务大小。
    max_per = settings.review_import_max_per_request
    if len(payload.reviews) > max_per:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"too many reviews in one request: {len(payload.reviews)} > {max_per}; "
                "split into smaller batches"
            ),
        )

    # 所有评论引用的 product 必须属于本店铺。
    product_ids = {r.product_id for r in payload.reviews}
    valid = set(
        db.execute(
            select(Product.id).where(
                Product.shop_id == payload.shop_id, Product.id.in_(product_ids)
            )
        ).scalars().all()
    )
    invalid = product_ids - valid
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"product_id not in shop: {sorted(invalid)}",
        )

    # 评论引用的 order（若有）也必须属于本店铺，禁止跨租户/跨店铺关联；
    # 同时把不存在的 order_id 在此拦截，避免 commit 时的 IntegrityError -> 500。
    order_ids = {r.order_id for r in payload.reviews if r.order_id is not None}
    if order_ids:
        valid_orders = set(
            db.execute(
                select(Order.id).where(
                    Order.shop_id == payload.shop_id, Order.id.in_(order_ids)
                )
            ).scalars().all()
        )
        invalid_orders = order_ids - valid_orders
        if invalid_orders:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"order_id not in shop: {sorted(invalid_orders)}",
            )

    # Core 批量插入（Phase 8）：请求级原子 —— 同一事务内按 chunk_size 分块 executemany，
    # 末尾一次 commit；任一块异常整体 rollback、零写入（保持「成功即返回插入条数」语义）。
    rows = [
        {
            "shop_id": payload.shop_id,
            "product_id": r.product_id,
            "order_id": r.order_id,
            "buyer_name": r.buyer_name,
            "rating": r.rating,
            "review_text": r.review_text,
        }
        for r in payload.reviews
    ]
    chunk_size = settings.bulk_insert_chunk_size
    try:
        for start in range(0, len(rows), chunk_size):
            db.execute(insert(Review), rows[start : start + chunk_size])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(rows)


def list_reviews(
    db: Session,
    account_id: int,
    limit: int,
    cursor: int | None,
    shop_id: int | None = None,
    product_id: int | None = None,
) -> tuple[list[Review], int | None]:
    stmt = (
        select(Review)
        .join(Shop, Review.shop_id == Shop.id)
        .where(Shop.account_id == account_id)
    )
    if shop_id is not None:
        get_owned_shop(db, account_id, shop_id)
        stmt = stmt.where(Review.shop_id == shop_id)
    if product_id is not None:
        stmt = stmt.where(Review.product_id == product_id)
    return keyset_paginate(db, stmt, Review.id, limit, cursor)


def enqueue_classification(
    db: Session, account_id: int, payload: ReviewClassifyBatchRequest
) -> tuple[int, int, int]:
    """选待分类评论 → 切块 → 每块建 agent_task 入队 llm 队列。

    返回 (enqueued_chunks, enqueued_reviews, remaining)。remaining 为符合条件
    但本次未入队的评论数（受 max_per_request 上限影响）；不静默截断。
    """
    # 惰性 import 避免与 tasks 的潜在环
    from app.agents.review_batch_classifier import ReviewBatchClassifierAgent
    from app.tasks.llm import classify_reviews_batch_task

    get_owned_shop(db, account_id, payload.shop_id)

    # 覆盖只走 force：非 force 必须只分类未分类评论，否则该组合无意义（选已分类却不覆盖）
    if not payload.force and not payload.only_unclassified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only_unclassified=false requires force=true",
        )

    max_per = settings.llm_classify_max_per_request
    take = min(payload.limit or max_per, max_per)

    conds = [Review.shop_id == payload.shop_id]
    # 非 force：只选未分类（与回写侧 IS NULL 条件一致）；force：含已分类，回写时覆盖
    if not payload.force:
        conds.append(Review.review_type.is_(None))

    total = db.execute(
        select(func.count()).select_from(Review).where(*conds)
    ).scalar_one()
    ids = list(
        db.execute(
            select(Review.id).where(*conds).order_by(Review.id).limit(take)
        ).scalars().all()
    )
    remaining = max(0, total - len(ids))

    chunk_size = settings.llm_classify_chunk_size
    enqueued_chunks = 0
    enqueued_reviews = 0
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start : start + chunk_size]
        digest = hashlib.sha1(
            ",".join(str(i) for i in sorted(chunk)).encode()
        ).hexdigest()
        idem = (
            f"review.classify_batch:{payload.shop_id}:"
            f"{ReviewBatchClassifierAgent.prompt_version}:{digest}"
        )
        if payload.force:
            idem += f":{uuid.uuid4().hex}"

        task, created = task_svc.create_task(
            db,
            task_type="review.classify_batch",
            queue_name="llm",
            idempotency_key=idem,
            account_id=account_id,
            shop_id=payload.shop_id,
            entity_type="review_batch",
            entity_id=None,
            input_json={"review_ids": chunk, "force": payload.force},
        )
        if not created:
            continue  # 幂等命中：该块已入队，跳过

        try:
            async_res = classify_reviews_batch_task.apply_async(args=[task.id])
            task_svc.set_celery_id(db, task.id, async_res.id)
        except Exception as exc:  # noqa: BLE001 - 入队失败留可见记录
            task_svc.mark_failed(
                db,
                task.id,
                error_type="EnqueueError",
                error_message=str(exc),
                error_detail="",
                from_statuses=(TaskStatus.PENDING,),
            )
            continue
        enqueued_chunks += 1
        enqueued_reviews += len(chunk)

    return enqueued_chunks, enqueued_reviews, remaining
