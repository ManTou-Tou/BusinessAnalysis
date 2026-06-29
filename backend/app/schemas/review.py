from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReviewImportItem(BaseModel):
    product_id: int
    order_id: int | None = None
    buyer_name: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    review_text: str


class ReviewImportRequest(BaseModel):
    """批量导入某店铺评论。"""

    shop_id: int
    reviews: list[ReviewImportItem] = Field(min_length=1)


class ReviewImportResult(BaseModel):
    imported: int


class ReviewClassifyBatchRequest(BaseModel):
    """批量触发评论分类。"""

    shop_id: int
    limit: int | None = Field(default=None, ge=1)  # 本次最多入队多少条（受服务端上限约束）
    only_unclassified: bool = True  # 仅分类 review_type 为空的评论
    force: bool = False  # 强制重新分类（含已分类）；优先于 only_unclassified


class ReviewClassifyBatchResult(BaseModel):
    enqueued_chunks: int
    enqueued_reviews: int
    remaining: int  # 符合条件但本次未入队的评论数（不静默截断）


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shop_id: int
    product_id: int
    order_id: int | None
    buyer_name: str | None
    rating: int | None
    review_text: str
    review_type: str | None
    sentiment: str | None
    created_at: datetime
