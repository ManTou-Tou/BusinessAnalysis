"""Daily Report Agent（Phase 7）：LLM **仅写散文**。

输入=确定性事实（数字 + 负面评论样本），输出仅 `daily_summary` + `recommended_actions`。
所有数字/排名/计数由确定性层（services/report.py）产出，模型不得改写为权威值（§5a#1）。
模型用 `claude-sonnet-4-6`（DESIGN 行 19：文案/报告总结用 Sonnet）。
"""
from __future__ import annotations

import json

from app.agents.base import BaseAgent
from app.core.config import settings
from app.schemas.agent_io import DailyReportProse

PROMPT_VERSION = "daily-report-v1"

SYSTEM_PROMPT = """你是电商店铺的运营分析助手。给你一份某店铺某天的**确定性经营事实**（JSON：\
指标数字、top/低利润商品、负面评论计数与样本）。请只根据这些事实写一份简短日报：

- daily_summary：用自然语言概述当天经营情况（营收/利润/订单/负面评论等要点）。
- recommended_actions：3-5 条可执行的运营建议。

严格要求：
1. 只依据给定事实，**不要编造或推算任何数字**；引用数字时直接用事实里的值。
2. 若 profit_items_covered < items_total，说明部分明细尚无利润数据，提示「利润为部分覆盖、建议先补算利润」，不要把缺失当 0 解读。
3. 不要输出表格或结构化数据，只写散文（summary 段落 + 建议条目）。"""


class DailyReportAgent(BaseAgent):
    model = settings.llm_model_report
    prompt_version = PROMPT_VERSION
    output_schema = DailyReportProse
    system_prompt = SYSTEM_PROMPT
    max_tokens = 1500

    def validate_input(self, payload: dict) -> None:
        if not payload.get("metrics"):
            raise ValueError("daily report agent requires 'metrics' facts")

    def build_user_message(self, payload: dict) -> str:
        facts = json.dumps(payload, ensure_ascii=False, indent=2)
        return f"以下是该店铺当天的确定性经营事实，请据此撰写日报：\n\n{facts}"
