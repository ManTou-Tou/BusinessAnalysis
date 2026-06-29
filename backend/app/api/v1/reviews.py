"""Review 路由（批量导入 + 列表；分类在 Phase 5）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import PageParams, get_account_id
from app.core.config import settings
from app.core.db import get_db
from app.core.ratelimit import RateLimit
from app.schemas.common import Page
from app.schemas.review import (
    ReviewClassifyBatchRequest,
    ReviewClassifyBatchResult,
    ReviewImportRequest,
    ReviewImportResult,
    ReviewOut,
)
from app.services import review as review_service

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post(
    "/import",
    response_model=ReviewImportResult,
    status_code=201,
    dependencies=[Depends(RateLimit("reviews.import"))],
)
def import_reviews(
    payload: ReviewImportRequest,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> ReviewImportResult:
    imported = review_service.import_reviews(db, account_id, payload)
    return ReviewImportResult(imported=imported)


@router.post(
    "/classify",
    response_model=ReviewClassifyBatchResult,
    status_code=202,
    dependencies=[
        Depends(RateLimit("reviews.classify", limit=settings.ratelimit_llm_trigger_limit))
    ],
)
def classify_reviews(
    payload: ReviewClassifyBatchRequest,
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> ReviewClassifyBatchResult:
    """批量分类：选待分类评论 → 切块入队 llm 队列。异步执行，状态查 /agent/tasks/{id}。"""
    chunks, reviews, remaining = review_service.enqueue_classification(
        db, account_id, payload
    )
    return ReviewClassifyBatchResult(
        enqueued_chunks=chunks, enqueued_reviews=reviews, remaining=remaining
    )


@router.get("", response_model=Page[ReviewOut])
def list_reviews(
    params: PageParams = Depends(),
    shop_id: int | None = Query(default=None),
    product_id: int | None = Query(default=None),
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> Page[ReviewOut]:
    items, next_cursor = review_service.list_reviews(
        db, account_id, params.limit, params.cursor, shop_id, product_id
    )
    return Page(items=items, next_cursor=next_cursor)
