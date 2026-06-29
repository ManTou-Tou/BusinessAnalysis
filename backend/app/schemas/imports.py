"""Excel 导入 schema（Phase 9）。

触发接口返回 AgentTaskOut（任务 id + 状态）；状态/结果查 GET /agent/tasks/{id}，
其 output_json 含导入统计与行级错误报告。
"""
from __future__ import annotations

from typing import Literal

# 支持的实体与冲突策略。
ImportEntity = Literal["shops", "products", "orders", "reviews"]
ImportConflict = Literal["upsert", "insert"]

IMPORT_ENTITIES: tuple[str, ...] = ("shops", "products", "orders", "reviews")
