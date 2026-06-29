# Phase 4 方案：Agent Framework + 首个真实 LLM Agent

> **状态：待用户过审 + Codex 过审。两者通过前不写代码。**
> 规则见 CLAUDE.md / CODEX_REVIEW_POLICY.md。设计总纲见 DESIGN.md，运行底座见 PHASE_3_PLAN.md。

---

## 1. 本阶段做什么

在 Phase 3 的异步任务底座上，建立 **Agent 框架** 并接入**第一个真实的 Claude Agent**，把「入队 → LLM 调用 → 结构化校验 → 结果落库 + 成本记录」整条链路打通。

按 DESIGN §5「Phase 4 只服务一个真实 Agent，不过度抽象」，本阶段的真实 Agent 选 **单条评论分类（Review Classifier，单条版）**——它是 MVP 内的 Agent、输入输出结构化、价值清晰。Phase 5 再把它扩成「批量 + 限流 + 1万/分钟吞吐」。

范围：

1. **LLM 客户端封装** `app/agents/llm.py`：封装官方 `anthropic` SDK，提供「给定 system/user + 输出 schema → 返回校验过的结构化对象 + token 用量」的统一入口。
2. **BaseAgent** `app/agents/base.py`：抽象基类，约定 `input_schema` / `output_schema`（Pydantic）、`model`、`prompt_version`、`build_messages()`、`run()`、`handle_error()`，并把 `token_usage / cost / model / prompt_version` 落到 `agent_tasks`。
3. **Review Classifier Agent（单条）** `app/agents/review_classifier.py`：输入一条评论，输出 `review_type / sentiment / summary / need_reply`（Pydantic 校验）。模型用 `claude-haiku-4-5`。
4. **LLM 队列任务** `app/tasks/llm.py`（queue=`llm`）：`classify_review` 任务，经 Phase 3 的 `run_lifecycle` 执行；成功后把 `review_type / sentiment` 写回 `reviews` 行，并把成本/用量记到 `agent_tasks`。
5. **API**：`POST /api/v1/agent/review-classifier`（对应 project.md），入参 `review_id`，校验租户归属，建 `agent_tasks`（`task_type=review.classify`、`shop_id`、`entity_type=review`、`entity_id=review_id`）并入队，返回业务任务 id。
6. **配置**：模型 id 进 `settings`（分类默认 `claude-haiku-4-5`，可配）；`ANTHROPIC_API_KEY` 已存在。LLM 单价表放代码常量用于算成本。
7. **任务注册**：把 `review.classify → classify_review` 加进 Phase 3 maintenance 的重入队 registry，使孤儿回收能重投该任务。

涉及文件（预估）：
- 新增：`app/agents/__init__.py`、`app/agents/llm.py`、`app/agents/base.py`、`app/agents/review_classifier.py`、`app/tasks/llm.py`、`app/schemas/agent_io.py`
- 改动：`app/api/v1/agent_tasks.py`（加 review-classifier 端点，或新建 `app/api/v1/agents.py`）、`app/api/v1/__init__.py`、`app/tasks/maintenance.py`（registry 加 review.classify）、`app/core/config.py`（模型 id 配置）、`backend/README.md`
- 不改 schema/迁移：复用 Phase 2 的 `reviews` 表（`review_type / sentiment` 字段已预留）与 Phase 3 的 `agent_tasks`。

---

## 2. 为什么用这种逻辑跑（运行 / 数据流 / 编排的理由）

**为什么 Agent 跑在 Celery `llm` 队列里，而不是 API 同步调用？**
LLM 调用是慢操作（数百毫秒到数秒），且受外部速率限制。复用 Phase 3 的异步底座：API 只建 `pending` 记录并入队、立即返回任务 id；worker 在 `llm` 队列异步调 Claude，结果落库；前端轮询 `GET /api/v1/agent/tasks/{id}`。`llm` 队列独立，将来可单独限流/扩 worker，不拖累 CRUD。

**为什么先做「单条」分类，而不是直接批量？**
Phase 4 的目标是验证**框架本身**：BaseAgent 契约、LLM 封装、结构化校验、成本记录、与任务生命周期的衔接。单条最简单、最易排查。把「batch prompt + 1万/分钟吞吐 + 限流」留给 Phase 5，能把「框架 bug」和「吞吐/批处理 bug」分开，降低风险（与 Phase 3 先做 noop 任务同理）。

**为什么 LLM = Claude，分类用 Haiku 4.5？**
技术栈已定 Claude。评论分类是高量、结构化、判断简单的任务，`claude-haiku-4-5`（输入 $1 / 输出 $5 每百万 token，200K 上下文）性价比最高、最快；复杂的文案/分析/报告才用 `claude-sonnet-4-6`（$3/$15）或 Opus 4.8（$5/$25）。模型 id 走配置，便于将来调整。

**数据流：**
```
POST /agent/review-classifier {review_id}
  → 校验 review 属当前 account 的 shop
  → 建 agent_tasks(pending, task_type=review.classify, entity=review:id) + 入队(llm 队列) + 回填 celery_task_id
  → 返回业务任务 id
worker(llm):
  claim_running → 读 review → ReviewClassifierAgent.run(评论内容)
    → llm.complete_structured(haiku, system, user, output_schema=ReviewClassification)
    → 校验输出 → 写回 reviews.review_type / sentiment
    → mark_succeeded + 记 model/prompt_version/token_usage/cost
```

**为什么结果要落回 `reviews` 表，又记一份到 `agent_tasks`？**
`reviews.review_type / sentiment` 是**业务事实**（供查询、统计、日报用）；`agent_tasks` 是**这次任务的审计**（哪个模型、prompt 版本、花了多少 token/钱、耗时）。两者职责不同：前者驱动业务，后者用于排障与成本核算。

---

## 3. 为什么要这样 build 代码（结构 / 取舍 / 权衡的理由）

**为什么要 BaseAgent 抽象，而不是把逻辑写进任务里？**
每个 Agent 都要做同样的事：校验输入 → 组 prompt（带版本）→ 调 LLM → **强制校验输出** → 记成本/用量 → 处理错误。抽到 `BaseAgent`，子类只写「prompt 模板 + 输入/输出 schema + 选模型」，既减少重复，又保证所有 Agent 的可观测字段一致（DESIGN §5 的 BaseAgent 契约）。这层只管「Agent 执行约定」，任务生命周期仍由 Phase 3 的 `run_lifecycle` 负责——分层不重叠。

**为什么用 `client.messages.parse(output_format=PydanticModel)` 做结构化输出？**
官方 SDK 的 `messages.parse()` 用 structured outputs 约束模型只能产出符合 schema 的 JSON，并自动校验/反序列化为 Pydantic 对象（失败可捕获）。这正是 DESIGN「LLM 输出必须 Pydantic 校验，失败时保存原始输出 + 错误」的落地方式——避免「模型自由发挥的文本解析不出来」这类脆弱性。输出 schema 用 `Literal` 限定 `review_type`（positive/negative/logistics_issue/quality_issue/price_issue/feature_question/spam）和 `sentiment`（positive/neutral/negative），与 Phase 2 `reviews` 字段口径一致。

**为什么 LLM 封装单独成层（`agents/llm.py`），不在每个 Agent 里直接 new client？**
集中一个地方管：client 初始化、模型选择、超时/重试策略、从 `response.usage` 取 `input_tokens/output_tokens`、按单价表算 `cost`、prompt 版本注入。换模型、加缓存、改限流都只改这一处；Agent 只管业务语义。

**为什么成本/用量在 LLM 封装算、由 BaseAgent 写库？**
`response.usage` 只有 LLM 封装拿得到；算成本是确定性逻辑（token × 单价），适合放在封装里返回 `(parsed, usage, cost)`。BaseAgent 再把这些连同 `model/prompt_version` 交给任务层落 `agent_tasks`——保持「LLM 细节在封装、落库在统一生命周期」的边界。

**与现有结构一致**：沿用 api → service/agent → model 分层；任务的 DB 写入仍走 `services/agent_task.py` 的条件门禁（`mark_succeeded` 仅 running→succeeded），不破坏 Phase 3 的并发安全。

---

## 4. 验收标准（Phase 4）

- 配好 `ANTHROPIC_API_KEY` 后，对一条已导入的评论调 `POST /api/v1/agent/review-classifier`，返回任务 id。
- worker（`llm` 队列）跑完后：`agent_tasks` 该任务 `succeeded`，记录了 `model / prompt_version / token_usage / cost / duration_ms`；对应 `reviews` 行的 `review_type / sentiment` 被正确填充。
- LLM 返回不符合 schema 时：任务落 `failed`/`retry`，`error_*` 保存原始错误（不写脏数据进 reviews）。
- 越权（review 不属当前 account 的 shop）：API 返回 404，不入队。
- 启动 worker 命令含 `llm` 队列：`celery -A app.core.celery_app:celery_app worker -Q default,llm,sync`。

---

## 5a. 实施时必须钉死（Codex 第 1 轮审阅要点）

1. **LLM 无有效输出即失败，绝不脏写**：`refusal` / `max_tokens` / API 错误 / schema 校验失败 → 任务进 `retry`/`failed` 并存 `error_*`，**不得回写 `reviews`**。
2. **事务边界**：`reviews` 回写与 `agent_tasks` 成功标记应在**同一 DB 事务**内提交，避免「review 已更新但 task failed」或「task succeeded 但 review 未更新」。（与 Phase 3 的条件门禁 `mark_succeeded` 仅 running→succeeded 结合实现。）
3. **幂等键** = `review.classify:{review_id}:{prompt_version}`；并提供**手动 force/reclassify** 入口（prompt 升级后旧幂等键不应挡住重跑）。
4. **双侧越权校验**：API 侧校验 review 属当前 account 的 shop；worker 侧再次按 `agent_tasks.shop_id` 与 `review` 归属复核，防队列污染/历史脏数据。
5. **模型 id**：配置同时支持 alias（`claude-haiku-4-5`）与 pinned id（`claude-haiku-4-5-20251001`）；生产默认用 pinned id，便于复现与成本审计。
6. **`summary` / `need_reply` 只进 `agent_tasks.output_json`**，本阶段不为它们扩 `reviews` 表（将来产品化再迁移）。

## 5. 明确不在本阶段做（留待后续）

- 批量分类、1 万/分钟吞吐、`llm` 队列限流（Phase 5）。
- 其他 Agent：Profit（确定性计算，Phase 6）、Daily Report（Phase 7）、Listing Writer / Product Analysis / Reply Generator（未来）。
- 每店铺/每天 token 预算与熔断（设计预留；本阶段只**记录**成本，不强制上限）。
- prompt 的集中管理系统（本阶段 prompt 内联在 Agent 内并带 `prompt_version` 常量即可）。
