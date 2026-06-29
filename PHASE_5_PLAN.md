# Phase 5 方案：Review Classifier 批量化（吞吐 + 限流）

> **状态：用户过审 + Codex 过审均已通过（APPROVED，2026-06-20）。代码已实现，见 backend/README.md「Phase 5」。**
> 规则见 CLAUDE.md / CODEX_REVIEW_POLICY.md。底座见 PHASE_3_PLAN.md（异步任务）、PHASE_4_PLAN.md（Agent 框架）。

---

## 1. 本阶段做什么

把 Phase 4 的「单条评论分类」扩成**可批量、可限流、可扩吞吐**的形态，目标对齐 project.md 的「支持 10000 reviews/minute」。核心是两件事：**一次 LLM 调用分类多条评论（batch prompt）** + **把工作切块 fan-out 到 `llm` 队列并发执行**。

范围：

1. **批量输出 schema** `app/schemas/agent_io.py` 增加 `ReviewClassificationBatch`：`items: list[ReviewClassificationItem]`，每项含 `index`（本批内序号）+ Phase 4 的 `review_type/sentiment/summary/need_reply`。
2. **批量 Agent** `app/agents/review_batch_classifier.py`：把 N 条评论以带序号的列表喂给模型，要求逐条输出（按 `index` 对齐），模型仍用 `claude-haiku-4-5`。
3. **批量任务** `app/tasks/llm.py` 增 `classify_reviews_batch_task`（queue=`llm`）：一个任务 = 一个「块」= 一次 LLM 调用。**LLM 调用在事务外**（只读 session）；调用返回并校验后，**在同一事务内**逐条尝试回写（成功条目写 `reviews`，并发冲突条目记 `output_json.skipped`）并标记任务成功，整块 token/cost 记入该任务的 `agent_tasks`。
4. **批量入口 API** `POST /api/v1/reviews/classify`（对应 project.md「Review APIs: POST /reviews/classify」）：入参 `shop_id`（校验归属）+ 可选 `limit`、`only_unclassified`（默认 true）、`force`（默认 false，与 only_unclassified 的覆盖语义见 §5a#3）；选出待分类评论 → 切块（chunk_size 可配）→ 每块建一个 `agent_tasks`（`task_type=review.classify_batch`）并入队 → 返回 `{enqueued_chunks, enqueued_reviews, remaining}`。
5. **限流与吞吐配置**：`llm` 队列任务设 `rate_limit`（每 worker 进程），chunk_size、单次请求上限 `max_reviews_per_request` 进 `settings`；启动 worker 用 `-Q ...,llm` 并按需调 `--concurrency`。
6. **任务注册**：`review.classify_batch → classify_reviews_batch_task` 加进 Phase 3 maintenance 重入队 registry。

涉及文件（预估）：
- 新增：`app/agents/review_batch_classifier.py`
- 改动：`app/schemas/agent_io.py`（批量 schema）、`app/tasks/llm.py`（批量任务 + finalize）、`app/services/review.py`（选「待分类」评论：`review_type IS NULL`，按 shop + keyset）、`app/api/v1/reviews.py`（POST /reviews/classify）、`app/schemas/review.py`（批量请求/响应 schema）、`app/tasks/maintenance.py`（registry）、`app/core/config.py`（chunk_size / rate_limit / max_per_request）、`backend/README.md`
- 不改迁移：复用 `reviews`（`review_type/sentiment` 已存在）与 `agent_tasks`。

---

## 2. 为什么用这种逻辑跑（运行 / 数据流 / 编排的理由）

**为什么要 batch prompt，而不是 Phase 4 的一条一调？**
1 万条/分钟若每条一次 LLM 调用，需要 ~1 万次/分钟调用——成本高、轮次多、极易撞 API 速率限制。把 N 条（如 25）合进一次调用，调用数降到 ~400/分钟，单条开销（系统 prompt、连接、思考）被摊薄。这正是 DESIGN §5「绝不一条一次调用」的要求。

**为什么要 fan-out 切块到 `llm` 队列，而不是一个大任务循环？**
单任务串行跑完 1 万条会很久且无法并行。切成多个「块任务」投到 `llm` 队列，由多个 worker 并发执行——吞吐 ≈ chunk_size × 并发 worker 数 × 每分钟调用数。每块独立、互不影响，失败只重试该块。

**为什么限流？**
Anthropic API 有按账号档位的速率限制；worker 一扩容就可能打爆外部服务或烧钱。给 `llm` 队列任务设 `rate_limit` + 控 worker 并发，把请求速率压在档位内，平滑突发。**真实吞吐取决于 Anthropic 档位与 worker 数**，本阶段提供可调旋钮并诚实记录上限，不硬性保证 1 万/分钟。

**数据流：**
```
POST /reviews/classify {shop_id, limit?, only_unclassified?=true, force?=false}
  → 校验 shop 属当前 account
  → 选待分类评论（only_unclassified 时 review_type IS NULL；force 时含已分类；按 shop，至多 max_reviews_per_request）
  → 切块(chunk_size) → 每块建 agent_tasks(pending, task_type=review.classify_batch,
       shop_id, entity_type=review_batch, input_json={review_ids:[...]}) + 入队(llm)
  → 返回 {enqueued_chunks, enqueued_reviews, remaining}
worker(llm, 每块一任务):
  claim_running → 读该块 review_ids（worker 侧二次校验都属本 shop）
    → BatchAgent.run(带序号的评论列表) → 校验输出条数/index 覆盖齐全
    → 同一事务：逐条尝试回写（成功写 reviews.review_type/sentiment，冲突记 skipped）+ mark_succeeded + 记 token/cost
```

**为什么按「块」原子、而不是按「单条」？**
块是 LLM 调用与计费的天然单位（一次调用一份 usage）。校验通过后，**同一事务**提交：成功条目的回写 + skipped 记录 + 任务成功标记一起落库（要么都提交，要么都回滚）；只有 §5a#2 列出的真异常才整块回滚重试，行级「已被并发分类」按 skipped 处理、不算失败。块开小（如 25）即可把失败的「爆炸半径」限制在小范围。

---

## 3. 为什么要这样 build 代码（结构 / 取舍 / 权衡的理由）

**为什么用「本批序号 index」对齐结果，而不让模型回显数据库 id？**
让模型回显业务主键容易出错（漏条、串号、编造）。改为我方给每条评论一个本批内 `index`（1..N），模型按 index 逐条输出；我方用 `index → review_id` 映射回写。落库前**强制校验**：输出条数 == 输入条数、index 覆盖 1..N 无重复无缺失；不满足即 raise → 整块回滚失败（不脏写）。这是把「LLM 输出必须 schema 校验 + 不可信」落到批量场景。

**为什么复用 Phase 3/4 的生命周期与 `finalize_succeeded`？**
块任务同样跑在 `run_lifecycle`（条件门禁、重试/超时、孤儿回收）里；成功路径用 `finalize_succeeded` 的 `side_effect` 一次性写回多条 reviews——并发安全与可观测性与前两阶段完全一致，不引入新机制。`side_effect` 里对每条更新带 `shop_id` 条件；行级 `rowcount==0` 按 §5a#2 记入 `output_json.skipped` 并继续（**不 raise**），整块只在 §5a#2 列出的真异常（读阶段越权/缺失、LLM 输出条数/index 不齐）时失败回滚。

**为什么批量与单条用两个 Agent？**
单条 Agent（Phase 4）与批量 Agent 的 prompt、输出 schema 不同；分开避免一个类塞两种模式。两者都基于 `BaseAgent`，复用 `llm.complete_structured`。

**为什么切块在 API 同步做，但设上限？**
切块 + 批量建任务放在请求里实现简单直接；但为防一次请求建过多任务拖慢请求/堆积队列，设 `max_reviews_per_request` 上限，返回 `remaining` 告知还剩多少未入队（**不静默截断**）。客户端可分多次调用消费完。超大规模的「自动全量分发」留待后续（可由定时任务或专门 dispatcher 承担）。

**chunk_size 取舍**：太大 → 单次 prompt 长、输出长、单块失败影响多条、易撞 `max_tokens`；太小 → 调用数多、摊薄不足。默认折中（如 25，可配），并据评论长度与模型上限调整。

---

## 4. 验收标准（Phase 5）

- 给某 shop 导入若干（如 100）条未分类评论，`POST /api/v1/reviews/classify {shop_id}` 返回 `enqueued_chunks/enqueued_reviews/remaining`。
- worker（`llm` 队列）跑完后：相应 `reviews` 行的 `review_type/sentiment` 被填；每个块任务 `agent_tasks` 为 `succeeded` 且记录了 `token_usage/cost/model/prompt_version`。
- 单块 LLM 输出条数/index 不齐 → 该块任务 `retry`/`failed`，块内评论保持未分类（不脏写、不半写）。
- 并发冲突：only_unclassified 路径下，块内某条已被并发分类 → 记入 `output_json.skipped`，该块仍 `succeeded`，不毒性重试。
- `force=true`：重新分类已分类评论并覆盖 `review_type/sentiment`；与 `only_unclassified` 同传时以 `force` 为准。
- 越权：API 侧 shop 不属当前 account → 404；worker 侧二次校验 review 属本 shop。
- 限流可见：`llm` 队列任务的 `rate_limit` 生效，调用速率被压制（日志/Flower 可观察）。
- 单次请求超 `max_reviews_per_request` 时，`remaining` 正确反映未入队数量。

---

## 5a. 实施时必须钉死（Codex 第 1 轮审阅要点）

1. **LLM 调用不在 DB 事务内**：worker 先在只读 session 里读 review_ids + 调 LLM（慢操作，不占事务/连接）；事务只覆盖「校验通过 → 逐条回写 + mark 成功」，由 `finalize_succeeded` 的 `side_effect` 完成。
2. **并发覆盖防护 + 防毒性重试（关键）**：每条回写 `UPDATE` 带 `Review.id == id AND Review.shop_id == shop_id`，only_unclassified 路径再加 `Review.review_type IS NULL`。
   - **`rowcount == 0` 视为「跳过/冲突」，不 raise、不失败**：说明该评论在 LLM 调用期间已被其他任务分类（或 force 路径下评论已不存在）。把它记进 `output_json.skipped`，继续处理本块其余条目。否则该块会因为「已分类」而永远重试，形成毒性重试。
   - 整块**只在以下情况失败**（在写库之前就判定）：读阶段越权/评论缺失、或 LLM 输出条数/index 不齐。这些是真异常，进 retry/failed；行级「已被并发分类」不算异常。
   - 见 §5a#3 对 force 与 only_unclassified 的区分。
3. **`force` 与 `only_unclassified` 语义分开**（`force=true` 优先，覆盖 `only_unclassified`）：
   - 默认 `only_unclassified=true, force=false`：只选 `review_type IS NULL`，回写带 `review_type IS NULL` 条件（不覆盖已分类）；并发冲突按 §5a#2 跳过。
   - `force=true`：重新分类（含已分类），选取忽略 `IS NULL`，回写**不带** `IS NULL` 条件（允许覆盖），幂等键加 uniquifier 绕过去重；行 `rowcount==0`（评论已删）按 §5a#2 跳过，不毒性重试。
   两条路径的选取条件、回写条件、幂等键策略分开实现。
4. **幂等键定义**：`review.classify_batch:{shop_id}:{prompt_version}:{sha1(sorted(review_ids))}`；`force` 时追加 `:{uuid4}`。重复请求命中同键 → 不重复入队、不重复扣费（复用 Phase 3 `create_task` 的 IntegrityError→re-read）。
5. **限流口径说明**：Celery `rate_limit` 是**每 worker 进程**维度，多 worker/多进程会成倍放大总速率。要严格贴 Anthropic 账号级 quota，需后续做全局限流器/集中调度（本阶段不做，文档注明）。
6. **chunk_size 受 token 约束**：默认 25，但须受单次输入/输出 token 上限与单条评论长度约束；过长评论先截断到上限（如每条 review_text 截断到 N 字符），必要时按 token 动态缩小块；目的是不撞 `max_tokens`、输出能完整返回。
7. **`summary/need_reply` 进 `agent_tasks.output_json`**：批量任务的 `output_json` 存整块的逐条结果列表（含 summary/need_reply），保留审计价值；`reviews` 仍只写 `review_type/sentiment`。

## 5. 明确不在本阶段做（留待后续）

- 真实 Shopee/TikTok/Lazada 平台 API 对接。
- 「自动全量分发」（定时扫描全部未分类评论并持续分发）——本阶段靠分页调用 `/reviews/classify` 消费。
- 其他 Agent：Profit（Phase 6，确定性计算）、Daily Report（Phase 7）。
- 每店铺/每天 token 预算与熔断（设计预留；本阶段仍只记录成本）。
- 用 Anthropic Message Batches API（异步半价批处理）——本阶段用实时 messages + Celery 并发；Batches 留作后续成本优化选项。
