"""Agent 的输入/输出结构化 schema（强制 Pydantic 校验，供 LLM structured outputs 用）。"""
from typing import Literal

from pydantic import BaseModel, Field

# 与 reviews 表口径一致
ReviewType = Literal[
    "positive",
    "negative",
    "logistics_issue",
    "quality_issue",
    "price_issue",
    "feature_question",
    "spam",
]
Sentiment = Literal["positive", "neutral", "negative"]


class ReviewClassification(BaseModel):
    """Review Classifier 的输出（LLM 必须产出符合此 schema 的 JSON）。"""

    review_type: ReviewType = Field(description="评论类型")
    sentiment: Sentiment = Field(description="情感倾向")
    summary: str = Field(description="一句话摘要")
    need_reply: bool = Field(description="是否需要人工/客服回复（投诉、问题类为 true）")


class ReviewClassificationItem(BaseModel):
    """批量分类中的单条结果，按本批内序号 index 与输入对齐。"""

    index: int = Field(description="本批内序号，从 1 开始，与输入一一对应")
    review_type: ReviewType
    sentiment: Sentiment
    summary: str
    need_reply: bool


class ReviewClassificationBatch(BaseModel):
    """批量分类输出：逐条对应输入，条数与 index 必须齐全。"""

    items: list[ReviewClassificationItem]


class DailyReportProse(BaseModel):
    """Daily Report Agent 输出：**仅散文**，不含任何数字/排名/计数（§5a#1）。

    所有结构化 sections 与数值由确定性层产出；LLM 只根据给定事实写概述与建议。
    """

    daily_summary: str = Field(description="对当日经营的简短自然语言概述（依据给定事实，勿编造数字）")
    recommended_actions: list[str] = Field(
        description="3-5 条可执行的运营建议（散文条目，依据给定事实，勿编造数字）"
    )
