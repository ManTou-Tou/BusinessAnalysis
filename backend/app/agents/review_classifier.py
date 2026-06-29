"""Review Classifier Agent（单条版，Phase 4）。

输入一条评论，输出 review_type / sentiment / summary / need_reply。
批量 + 限流 + 高吞吐留待 Phase 5。
"""
from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.config import settings
from app.schemas.agent_io import ReviewClassification

PROMPT_VERSION = "review-classify-v1"

SYSTEM_PROMPT = """你是电商评论分类助手。给定一条买家评论，输出结构化分类。

review_type 取值定义：
- positive：正面好评
- negative：泛泛的负面评价（无具体原因）
- logistics_issue：物流/配送/发货相关问题
- quality_issue：商品质量/损坏/与描述不符
- price_issue：价格/优惠/退款金额相关
- feature_question：对商品功能/用法的提问
- spam：广告、无关或垃圾内容

sentiment：positive / neutral / negative。
summary：用一句话概括评论要点。
need_reply：是否需要人工或客服回复——投诉类（negative/logistics_issue/quality_issue/price_issue）与 feature_question 通常为 true；positive 与 spam 通常为 false。

只依据评论内容判断，不要臆测未提及的信息。"""


class ReviewClassifierAgent(BaseAgent):
    model = settings.llm_model_classify
    prompt_version = PROMPT_VERSION
    output_schema = ReviewClassification
    system_prompt = SYSTEM_PROMPT

    def validate_input(self, payload: dict) -> None:
        text = payload.get("review_text")
        if not text or not str(text).strip():
            raise ValueError("review_text is required and must be non-empty")

    def build_user_message(self, payload: dict) -> str:
        return f"请分类以下商品评论：\n\n{payload['review_text']}"
