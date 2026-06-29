"""LLM 客户端封装（Claude）。

集中管理：Anthropic client、结构化输出（messages.parse + Pydantic 校验）、
从 response.usage 取 token 用量、按单价表算成本。换模型/加缓存/改限流只改此处。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import anthropic
from pydantic import BaseModel

from app.core.config import settings


class LLMOutputError(Exception):
    """LLM 未产出符合 schema 的有效输出（refusal / max_tokens / 解析失败等）。"""


# 单价：USD per token（input, output）。键同时支持 alias 与 pinned id。
_PER_MTOK = {
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
    "claude-haiku-4-5-20251001": (Decimal("1"), Decimal("5")),
    "claude-sonnet-4-6": (Decimal("3"), Decimal("15")),
    "claude-opus-4-8": (Decimal("5"), Decimal("25")),
}
_MILLION = Decimal("1000000")


@dataclass
class LLMResult:
    parsed: BaseModel
    model: str
    input_tokens: int
    output_tokens: int
    cost: Decimal | None  # USD；未知模型为 None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _cost(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    price = _PER_MTOK.get(model)
    if price is None:
        return None
    return (price[0] * input_tokens + price[1] * output_tokens) / _MILLION


def complete_structured(
    *,
    model: str,
    system: str,
    user: str,
    output_schema: type[BaseModel],
    max_tokens: int = 1024,
) -> LLMResult:
    """调用 Claude 并强制结构化输出，返回校验过的对象 + 用量 + 成本。

    无有效输出（refusal / max_tokens / 解析失败）抛 LLMOutputError；
    API/网络错误抛 anthropic 异常 —— 均由任务生命周期捕获，进 retry/failed，不脏写。
    """
    client = _get_client()
    resp = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=output_schema,
    )
    parsed = resp.parsed_output
    if parsed is None:
        raise LLMOutputError(
            f"no valid structured output (stop_reason={resp.stop_reason})"
        )
    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    return LLMResult(
        parsed=parsed,
        model=resp.model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost=_cost(resp.model, in_tok, out_tok),
    )
