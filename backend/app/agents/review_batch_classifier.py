"""Review Batch Classifier Agent（Phase 5）。

一次 LLM 调用分类一批评论：输入带序号 index 的列表，要求逐条按 index 对应输出。
结果与 db 的对齐由调用方用 index→review_id 完成（不让模型回显 db id）。
"""
from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.config import settings
from app.schemas.agent_io import ReviewClassificationBatch

PROMPT_VERSION = "review-classify-batch-v1"

SYSTEM_PROMPT = """你是电商评论分类助手。给定一批带序号的买家评论，逐条分类。

review_type 取值定义：
- positive：正面好评
- negative：泛泛的负面评价（无具体原因）
- logistics_issue：物流/配送/发货相关问题
- quality_issue：商品质量/损坏/与描述不符
- price_issue：价格/优惠/退款金额相关
- feature_question：对商品功能/用法的提问
- spam：广告、无关或垃圾内容

sentiment：positive / neutral / negative。
summary：一句话概括该条评论。
need_reply：是否需要人工或客服回复——投诉类与 feature_question 通常为 true；positive 与 spam 通常为 false。

输出要求（务必遵守）：
- 对每个输入序号 index 输出且仅输出一条对应结果；
- 输出条目数必须与输入条数完全一致，index 必须覆盖每一条，不得遗漏、重复或新增；
- index 必须与输入中的方括号序号一致。
只依据评论内容判断，不要臆测未提及的信息。"""


class ReviewBatchClassifierAgent(BaseAgent):
    model = settings.llm_model_classify
    prompt_version = PROMPT_VERSION
    output_schema = ReviewClassificationBatch
    system_prompt = SYSTEM_PROMPT
    max_tokens = settings.llm_classify_batch_max_tokens

    def validate_input(self, payload: dict) -> None:
        reviews = payload.get("reviews")
        if not reviews:
            raise ValueError("reviews is required and must be non-empty")

    def build_user_message(self, payload: dict) -> str:
        lines = [f"[{r['index']}] {r['text']}" for r in payload["reviews"]]
        return "请对以下每条评论逐条分类，按 index 一一对应输出：\n\n" + "\n\n".join(lines)
