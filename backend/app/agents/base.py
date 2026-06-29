"""BaseAgent：统一 Agent 执行契约（DESIGN §5）。

子类只声明 model / prompt_version / output_schema / system_prompt，并实现
validate_input + build_user_message。run() 负责：校验输入 → 组 prompt → 调 LLM
（强制 Pydantic 输出校验）→ 返回 LLMResult（含用量/成本）。
任务生命周期（落库、重试、写业务表）由调用方处理，不在 Agent 内提交事务。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.agents import llm


class BaseAgent(ABC):
    model: str
    prompt_version: str
    output_schema: type[BaseModel]
    system_prompt: str
    max_tokens: int = 1024

    @abstractmethod
    def validate_input(self, payload: dict) -> None:
        """校验输入；不合法抛异常（→ 任务 failed/retry）。"""

    @abstractmethod
    def build_user_message(self, payload: dict) -> str:
        """根据输入构造发给模型的 user 消息。"""

    def run(self, payload: dict) -> llm.LLMResult:
        self.validate_input(payload)
        user = self.build_user_message(payload)
        return llm.complete_structured(
            model=self.model,
            system=self.system_prompt,
            user=user,
            output_schema=self.output_schema,
            max_tokens=self.max_tokens,
        )
