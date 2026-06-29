# Phase 7 方案：Daily Report（确定性聚合 + LLM 仅总结 + Beat 定时）

> **状态：方案 Codex APPROVED（经一轮整改）；代码已实现并经 Codex APPROVED（2026-06-20，经一轮整改）。Phase 7 完成。**
> 规则见 CLAUDE.md / CODEX_REVIEW_POLICY.md。设计总纲见 DESIGN.md（§3 LLM 只做该做的事、行 33/120/122/139/184、表 §90/103/104）。
> 底座见 PHASE_3_PLAN.md（任务生命周期）、PHASE_4_PLAN.md（BaseAgent + LLM 封装）、PHASE_6_PLAN.md（确定性计算 + order_items.profit）。

---

## 1. 本阶段做什么

生成**店铺每日运营报告**：先用 SQL/Python 把当天指标**确定性**算出来并落库，再让 LLM **仅根据这些既成事实写散文**（摘要 + 建议），最后存成可读报告。对齐 project.md「Agent 6: Daily Report」与「Analytics APIs」。**数字一律不来自 LLM**（DESIGN §3.18 / 行 139：SQL 聚合 + 模板，LLM 只总结重点、不做数据计算）。

范围：

1. **指标表迁移** `alembic/versions/0003_daily_metrics.py`（FK + 索引钉死，见 §5a#5）：新增
   - `shop_daily_metrics`：`shop_id, date, orders, revenue, profit, ad_spend, items_total, profit_items_covered, views(NULL), clicks(NULL), conversion_rate(NULL)`，`uniq(shop_id, date)` + `(date)`，FK `shop_id→shops.id`。
   - `product_daily_metrics`：`product_id, shop_id, date, units_sold, revenue, profit`，`uniq(product_id, date)` + `(shop_id, date)`，FK `shop_id→shops.id`、`product_id→products.id`。
   - `daily_reports`：`shop_id, date, daily_summary(TEXT), sections_json(JSON), model, prompt_version, source_task_id, generated_at`，`uniq(shop_id, date)`，FK `shop_id→shops.id`。
   金额一律 `DECIMAL(12,2)`；`views/clicks/conversion_rate` 暂无数据源 → 可空、不填（不假造，见 §5a#2）。
   `items_total/profit_items_covered` 为**明细级**口径（见 §5a#3）。
2. **确定性聚合服务** `app/services/report.py`（纯/半纯函数，DB 只读）：给定 `shop_id + date`，算出店铺级指标、商品级指标、`top_products`、`low_profit_products`、`negative_review_summary`（**计数/结构化**）等**全部数值与结构化事实**。这些是 `/report` 对外结构化 sections 的**唯一来源**，LLM 不参与生成（见 §5a#1）。
3. **Daily Report Agent（LLM 仅写散文）** `app/agents/daily_report.py`：基于 Phase 4 `BaseAgent` + `claude-sonnet-4-6`（DESIGN 行 19：文案/报告总结用 Sonnet）。输入=确定性事实（数字 + 少量负面评论样本），输出 schema **仅含两个散文字段**：`daily_summary` + `recommended_actions`。**不含 `negative_review_summary`、不含任何数字/排名/计数**——负面评论的总结/数量一律由确定性层产出（如需「负面散文」，让模型只在 `daily_summary` 里就给定事实展开，不另出结构化字段）。强制 Pydantic 校验。
4. **generate_daily_report 任务** `app/tasks/report.py`（queue=`llm`，因含 LLM 调用）：经 `run_lifecycle`；`_run` 只读聚合（关 session 后）→ 调 Agent（事务外）；`_finalize` 用 **`finalize_succeeded(side_effect=...)`**（不可用裸 `mark_succeeded`，否则副作用与成功不同事务）在**同一事务**内：`shop_daily_metrics` upsert（单行）+ `product_daily_metrics` **先按 (shop_id,date) 删旧再插当日全集**（防陈旧残留，见 §5a#3）+ `daily_reports` **带 `source_task_id` 守卫的条件 upsert**（防旧任务覆盖新报告，见 §5a#6）+ 标记成功，并记 `model/prompt_version/token_usage/cost`。
5. **触发 API** `POST /api/v1/agent/daily-report`（project.md「POST /agent/daily-report」）：入参 `shop_id`（校验归属）+ 可选 `date`（默认昨天 UTC）、`force`（默认 false）；建 `agent_tasks`（`task_type=daily.report`）入队，返回业务任务 id。
6. **读接口**（project.md「Analytics APIs」）：
   - `GET /api/v1/analytics/shop/{shop_id}/daily?date=` → 读 `shop_daily_metrics`（纯数字）；
   - `GET /api/v1/analytics/shop/{shop_id}/report?date=` → 读 `daily_reports`（散文 + sections）。
7. **Beat 定时**：`celery_app.conf.beat_schedule` 每日触发 `app.tasks.report.dispatch_daily_reports`（按 shop fan-out，为「昨天」逐店建 `generate_daily_report`）。**派发器不调 LLM、纯派发 → 走 `default` 队列**；只有 `generate_daily_report`（含 Sonnet 调用）走 `llm` 队列。**按具体任务名路由**（不按模块前缀），见 §5a#8。**MVP 单实例调度**；多实例防重锁延后（DESIGN 行 120）。
8. **任务注册**：`daily.report → generate_daily_report_task` 加进 maintenance 重入队 registry。
9. **配置**：`llm_model_report="claude-sonnet-4-6"`、`report_calc_version="v1"`、`report_top_n=5`、`report_low_profit_margin`（低利润阈值）、`report_negative_sample=20`（喂 LLM 的负面评论条数上限）。

涉及文件（预估）：
- 新增：`alembic/versions/0003_daily_metrics.py`、`app/models/daily_metrics.py`（3 个模型）、`app/services/report.py`、`app/agents/daily_report.py`、`app/tasks/report.py`、`app/schemas/report.py`、`tests/test_report_aggregation.py`
- 改动：`app/api/v1/agents.py`（+daily-report 端点）、`app/api/v1/analytics.py`（+ shop daily / report）、`app/services/analytics.py`（shop 读聚合）、`app/core/config.py`、`app/core/celery_app.py`（include + route + beat_schedule）、`app/tasks/maintenance.py`（registry）、`backend/README.md`、`.env.example`

---

## 2. 为什么用这种逻辑跑（运行 / 数据流 / 编排的理由）

**为什么「先确定性算数字，再让 LLM 写散文」？**
日报里的销量、营收、利润、负面评论数都是确定性事实，LLM 来算会出错、不可复现、还烧钱（DESIGN §3.18）。所以：数值层用 SQL/Python 算并落库（可被 `GET /daily` 直接查），LLM 只拿这些**既成事实**写「今日概况」「建议动作」这类自然语言。报告里出现的数字始终引用确定性层，不让模型自由发挥。

**为什么把指标落库（shop/product_daily_metrics），而不是每次现算？**
日报是「某一天」的快照，落库后可重复查询、支撑趋势/周报（后续）、并与散文报告解耦。`uniq(shop_id,date)`/`uniq(product_id,date)` 让重算是**幂等 upsert**（覆盖同一天），不产生重复行。

**为什么散文报告单独存 `daily_reports`，与指标表分开？**
指标表是**数字事实**（驱动查询/趋势），`daily_reports` 是**这次 LLM 生成的可读报告**（散文 + 结构化 sections + 模型/版本/成本审计）。两者职责不同、生命周期不同（重算 prompt 升级只动报告，不动历史数字口径）；分表避免把散文塞进数字表。`uniq(shop_id,date)` 便于 `GET /report` 直接定位。

**为什么 `generate_daily_report` 放 `llm` 队列、`dispatch_daily_reports` 放 `default`？**
`generate_daily_report` 含一次 LLM 调用（Sonnet）——慢、花钱、需限流/超时/重试，放 `llm` 队列与分类共享 LLM worker、统一控制（队列定位：`default`=普通快任务；`llm`=分类/日报/文案等 LLM 任务）；放 `default` 会让 LLM 慢任务卡住普通快任务。`dispatch_daily_reports` 只是遍历 shops 建任务、**不调 LLM**，属轻量派发 → 走 `default`，不占 LLM worker。聚合是 DB 只读，先于 LLM 调用完成。

**数据流：**
```
POST /agent/daily-report {shop_id, date?=昨天, force?=false}
  → 校验 shop 属当前 account
  → 建 agent_tasks(pending, task_type=daily.report, shop_id, input_json={date}) + 入队(llm)
  → 返回业务任务 id
worker(llm, generate_daily_report):
  claim_running
  _run（只读）：services/report.aggregate(shop_id, date) 算店铺/商品指标 + top/low + 负面计数
              + 取至多 report_negative_sample 条负面评论文本；关闭 session 后
              → DailyReportAgent.run(facts) 仅产出 daily_summary/recommended_actions（事务外 LLM）
  _finalize（finalize_succeeded，同一事务）：
              shop_daily_metrics upsert（单行，确定性→顺序无关）
              + product_daily_metrics：DELETE WHERE shop_id,date 后 INSERT 当日全集（无陈旧残留）
              + daily_reports 条件 upsert（仅当 VALUES(source_task_id) >= 现有才覆盖）
              + mark 成功 + 记 model/prompt_version/token_usage/cost

Beat（每日，单实例）：dispatch_daily_reports → 遍历 shops，为「昨天」逐店建 daily.report 任务
  （幂等键 daily.report:{shop}:{date}:{ver} 防重复入队）

GET /analytics/shop/{id}/daily?date=   → shop_daily_metrics（数字）
GET /analytics/shop/{id}/report?date=  → daily_reports（散文 + sections）
```

---

## 3. 为什么要这样 build 代码（结构 / 取舍 / 权衡的理由）

**为什么聚合逻辑抽到 `services/report.py`、与任务/LLM 解耦？**
聚合是确定性核心，抽成可单测的函数（喂订单/评论/利润 → 断言指标），不依赖 LLM/任务/事务。任务层只负责「读→调 Agent→落库」的编排（沿用 Phase 4/5/6 的 `_run/_finalize`）。

**为什么 LLM 输出 schema 只放散文字段、不放数字？**
防止「报告里的数字和指标表对不上」。结构化 sections（top_products/low_profit/negative 计数）由确定性层产出并存入 `daily_reports.sections_json`；LLM 只补 `daily_summary/recommended_actions`。`GET /report` 返回 = 确定性 sections + LLM 散文，数字永远以确定性层为准。

**为什么把「营收/订单/销量」与「利润」分两套口径聚合？（关键，别复用 Phase 6 analytics 的 profit 过滤）**
Phase 6 `services/analytics.py` 求商品利润时带了 `OrderItem.profit IS NOT NULL` 过滤——那是为「只统计已算利润的明细」。**日报不能照搬**：否则一旦某些明细没算利润，营收/订单/销量会凭空缩水。Phase 7 必须显式分开：
- `orders / units_sold / revenue / ad_spend` 来自**当天全部合格订单/明细**（不带 profit 过滤）；
- `profit` 只 Σ **`order_items.profit IS NOT NULL`** 的明细（与 Phase 6 口径一致）；
- 覆盖率来自「有/无 profit 的明细计数」。

**为什么利润覆盖用「明细级」而不是「订单级」？**
利润的事实源是 `order_items.profit`（明细级），一张订单可能部分明细已算、部分缺成本未算。用订单级 coverage（如「至少一条非空」或从 `SUM(profit)` 反推）会含糊且可能错。故钉死**明细级**：`items_total` = 当天该 shop 全部明细数，`profit_items_covered` = 其中 `profit IS NOT NULL` 的明细数；`profit` 只汇总已覆盖明细。未覆盖的**不当 0**，报告提示「N/M 明细尚无利润数据，建议先跑 profit-analysis」。

**为什么 `product_daily_metrics` 重算要「先删后插」、而 shop/report 用 upsert？**
`product_daily_metrics` 每天每个动销商品一行——重算时若某商品当天已无销量（订单取消/退货/归属变化），单靠 `uniq(product_id,date)` 的 upsert 只会更新「这次仍出现」的行，**旧行永远残留**导致历史虚高。故同事务内**先 `DELETE WHERE shop_id=? AND date=?` 再 INSERT 当日全集**（一次性重建当天该店商品快照）。`shop_daily_metrics` 是单行（`uniq(shop_id,date)`），`daily_reports` 也是单行，直接 upsert 即可，无陈旧集合问题。

**为什么 `daily_reports` upsert 要带 `source_task_id` 守卫？**
`force` 并发重跑可能乱序完成，旧任务的 LLM 结果可能覆盖新结果（数字确定性、顺序无关，但散文会被旧版盖掉）。故 `daily_reports` 存 `source_task_id`（= 生成它的 `agent_tasks.id`，单调递增），upsert 用条件表达式 `col = IF(VALUES(source_task_id) >= source_task_id, VALUES(col), col)`，**旧任务不能覆盖新报告**。指标表是确定性数字、顺序无关，普通 upsert 即可。

**为什么 Beat 只做单实例 MVP？**
Celery Beat 多实例会重复调度；分布式锁是额外工程。DESIGN 行 120 明确 MVP 单实例、锁延后。即便偶发重复触发，幂等键 + upsert 也能保证**不重复入队、不重复落库**（force 才新建），所以重复调度最坏只是多跑一次同结果，不脏写。

**与现有结构一致**：沿用 api→service/agent→model 分层、`run_lifecycle`、`finalize_succeeded(side_effect)` 同事务回写、双侧租户校验、idempotency_key + IntegrityError→re-read。

---

## 4. 验收标准（Phase 7）

- 造某 shop 某天的订单（已跑 Phase 6 利润）+ 评论，`POST /agent/daily-report {shop_id, date}` 返回任务 id；worker 跑完后：
  - `shop_daily_metrics` 该 (shop,date) 行的 `orders/revenue/profit/ad_spend/items_total/profit_items_covered` 与手算一致（营收/订单不受 profit 缺失影响）；
  - `product_daily_metrics` 为当天有动销的商品各一行（重算后无陈旧残留行）；
  - `daily_reports` 有 `daily_summary/recommended_actions`（散文，仅这两项来自 LLM）+ `sections_json`（top_products/low_profit/negative 计数，全部确定性）+ `model/prompt_version/source_task_id`；
  - `agent_tasks` 任务 `succeeded` 且记 `token_usage/cost/duration_ms`。
- `GET /analytics/shop/{id}/daily?date=` 返回数字指标；`GET /analytics/shop/{id}/report?date=` 返回散文 + sections；不存在的日期统一返回 **404**（§5a#8）。
- **LLM 失败**（refusal/max_tokens/schema 不符）→ 任务 retry/failed，`error_*` 保存；指标与报告**均不落库**（同事务），不留半成品。
- **重算幂等**：同 (shop,date) 再次默认触发命中幂等键、不重复；`force=true` 重算并**覆盖** upsert。
- **利润未计算**：当天有明细但 `order_items.profit` 为 NULL → 计入 `items_total` 但不计入 `profit_items_covered`，`profit` 只汇总已覆盖明细，营收/订单不缩水，报告提示覆盖缺口，不当 0。
- **重算无残留**：先造商品 A、B 当天有销量并生成日报；再让 B 当天订单全部取消后 `force` 重算 → `product_daily_metrics` 当天只剩 A，B 的旧行被删除。
- **乱序覆盖防护**：较新 `source_task_id` 写入的 `daily_reports` 不被较旧任务覆盖。
- **越权**：shop 不属当前 account → 404，不入队；worker 侧二次校验。
- **Beat**：dispatch 任务对每个 shop 为「昨天」入队一份；重复触发不产生重复任务/重复行。
- **单元测试**（聚合为重点，LLM 打桩）：营收/订单/销量不受 profit 缺失影响、profit 仅汇总非空明细、明细级 coverage（`items_total`/`profit_items_covered`，NULL 不当 0）、top/low 选取与排序确定性、负面评论计数、日期半开区间边界（仅当天）、product_daily_metrics 先删后插无残留、`source_task_id` 守卫、`daily_reports` 散文仅含 LLM 两字段、越权、LLM 失败整体不落库。

## 5a. 实施时必须钉死（Codex 第 1 轮审阅要点）

1. **LLM 只写散文、绝不产/复述数字（最强约束）**：LLM 输出 schema **只有** `daily_summary` 与 `recommended_actions` 两个散文字段；**没有** `negative_review_summary` 或任何 list/count/排名字段。`/report` 暴露的**每一个结构化 section**（top_products/low_profit_products/negative_review_summary 计数/分布）都由 `services/report.py` 确定性产出并存 `sections_json`，**不从 LLM 输出拷贝**。Prompt 里给模型的数字是「只读事实」，要求其转述但不得改写为权威值；落库与对外的数字永远以确定性层为准。强制 Pydantic 校验，失败 → 任务 retry/failed、不落库。
2. **无数据源的指标不假造**：`views/clicks/conversion_rate` 当前无采集来源 → 列可空、留 NULL，报告不编造；待接入流量数据再填（本阶段注明）。
3. **事务边界 + 写法钉死**：LLM 调用在 DB 事务外（`_run` 关 session 后调）；`_finalize` **必须用 `finalize_succeeded(side_effect=...)`**（裸 `mark_succeeded` 是独立提交、不保证与副作用同事务，禁用）。同一事务内顺序：① `shop_daily_metrics` upsert；② `product_daily_metrics` **`DELETE WHERE shop_id,date` 后 INSERT 当日全集**（防陈旧残留——退货/取消/归属变化导致旧商品行不再出现时被清除）；③ `daily_reports` 条件 upsert（见 §5a#6）；④ 标记成功。任一步失败整体回滚（不留「指标已写但报告失败」或反之）。
4. **幂等键 + force**：`idempotency_key = daily.report:{shop_id}:{date}:{report_calc_version}`；默认 `force=false` 命中复用、不重复入队/落库；`force=true` 追加 `:{uuid4}` 新建任务并覆盖。指标单行用 `INSERT ... ON DUPLICATE KEY UPDATE`（靠 `uniq`）。
5. **利润口径与覆盖（明细级、不当 0、与营收分离）**：
   - **营收/订单/销量/广告费**来自当天**全部合格订单/明细**，**不带 `profit IS NOT NULL` 过滤**（别复用 Phase 6 analytics 的过滤，否则缺利润时这些数会缩水）；
   - **利润** = Σ `order_items.profit`，**仅** `profit IS NOT NULL` 的明细；
   - **覆盖（明细级）**：`items_total` = 当天该 shop 全部明细数；`profit_items_covered` = 其中 `profit IS NOT NULL` 的明细数。未覆盖明细**不当 0**，报告提示缺口。订单级不作为覆盖口径。
6. **乱序覆盖防护（`daily_reports`）**：表存 `source_task_id`（= 生成它的 `agent_tasks.id`）。条件 upsert：`SET daily_summary=IF(VALUES(source_task_id)>=source_task_id, VALUES(daily_summary), daily_summary)`（其余被 LLM 影响的列同理），**旧任务不得覆盖更新的报告**。指标表数字确定性、顺序无关，普通 upsert 即可。
7. **迁移 FK + 索引钉死（租户与清理）**：三表均加 FK——`shop_daily_metrics.shop_id→shops.id`、`product_daily_metrics.shop_id→shops.id` 且 `product_id→products.id`、`daily_reports.shop_id→shops.id`；索引 `uniq(shop_id,date)`/`uniq(product_id,date)` + 支撑租户读的 `(shop_id,date)`/`(date)`。避免孤儿报告行在 shop/product 删除后残留、弱化租户假设。
8. **日期半开区间 + 时区**：日期过滤用**半开区间** `created_at >= {date} 00:00:00 AND created_at < {date+1} 00:00:00`，**不要** `DATE(created_at)=`（慢且易掩盖时区/会话错误）。MVP 用 **UTC 自然日**，默认= UTC 昨天；per-shop 时区延后。`GET /daily|/report` 对无数据日期统一返回 **404**（口径一致，不静默空对象）。
9. **双侧越权 + worker 复核**：API 校验 shop 属当前 account；worker 按 `agent_tasks.shop_id` 复核；所有聚合查询全部按 shop 过滤，商品/订单/评论只取本 shop。
10. **Beat 单实例 + 按具体任务名分队列 + registry**：`beat_schedule` 每日一次（如 02:00 UTC）跑 `dispatch_daily_reports`（走 `default`）；它为「昨天」逐店入队 `generate_daily_report`（走 `llm`）。**路由按具体任务名**配置（与现有 `celery_app.py` 一致是任务名级路由）：
    - `app.tasks.report.generate_daily_report → llm`
    - `app.tasks.report.dispatch_daily_reports → default`
    - **不要**用 `app.tasks.report.* → 某队列` 的前缀路由把两者归到一队。
    并把 **`daily.report → generate_daily_report_task` 加进 `maintenance._registry()`**（孤儿可重投）；`dispatch_daily_reports` 是 beat 派发器、不建 `agent_tasks` 记录、不入 registry。worker 需 `-Q default,llm,sync` 才能同时消费派发与生成。多实例锁延后；靠幂等键 + upsert + `source_task_id` 守卫兜底重复调度。dispatcher 入队失败处理与 Phase 3/5 一致（留可见失败记录）。
11. **喂 LLM 的负面评论截断**：最多取 `report_negative_sample` 条、每条按 `review_text_max_chars` 截断，防 prompt/token 爆；计数（negative count）仍以确定性全量为准。
12. **金额/比率精度**：金额 `Decimal`/`DECIMAL(12,2)`；`conversion_rate` 等比率口径与单位写进 README（本阶段不计算，仅预留）。

## 5. 明确不在本阶段做（留待后续）

- `views/clicks/conversion_rate` 的真实采集与计算（无数据源）。
- Beat 多实例分布式锁、per-shop 时区、补算历史区间（backfill）。
- 周报/月报/趋势对比、同比环比。
- 其他 Agent：Product Analysis / Listing Writer / Reply Generator。
- 前端报告展示页（前端阶段）。
- 用 Message Batches API 降本（实时 messages 已足够，日报量低）。
