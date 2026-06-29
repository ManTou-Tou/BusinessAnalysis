# 系统设计文档（DESIGN.md）— v3

> AI E-Commerce Copilot 的系统设计方案，供 Codex review。
> **状态：v4 — Phase 1–9 均已实现并全部经 Codex APPROVED（截至 2026-06-26）。MVP 后端 Agent 流水线（评论分类 / 利润 / 日报）已打通；Phase 8 高并发优化、Phase 9 Excel 数据导入已落地。**
> 规则见 CODEX_REVIEW_POLICY.md / CLAUDE.md；需求来源 project.md；各阶段方案见 `PHASE_<n>_PLAN.md`。

## 版本记录
- v1：初版设计。
- v2：采纳 Codex 第 1 轮意见 —— MVP 收敛、订单拆 order_items、agent_tasks 审计字段、金额 DECIMAL、平台字段预留、Celery 幂等/落库/队列隔离、利润与日报去 LLM 化、Agent 输出 schema 校验、多租户与 readiness。
- v3（2026-06-20）：回填 Phase 3–7 实现实况 —— agent_tasks 实际 schema/唯一约束、实际任务类型与「按任务名」队列路由、确定性利润与日报口径、`shop/product_daily_metrics` 与 `daily_reports` 表、引擎 `CLIENT_FOUND_ROWS`（rowcount=匹配行）语义、进度更新至 Phase 7 完成。
- v4（2026-06-26）：Phase 8 高并发优化（迁移 0004 索引体检、Redis 固定窗口 API 限流、`SET NX PX` 分布式锁、reviews 批量导入请求级原子、连接池走配置）；Phase 9 Excel 数据导入（迁移 0005 shops 唯一键、0006 `agent_tasks.account_id` 授权列、`POST /imports/{entity}` 异步 sync 队列、products/orders/shops upsert + reviews append-only 断点续跑 + orders 相邻预扫描、openpyxl 流式）。两阶段方案 + 代码均经 Codex APPROVED。

---

## 0. 设计原则（本轮明确）

1. **MVP 做减法**：先把「数据闭环 + 一个可靠 Agent + 可观测异步任务」做扎实，不一次性铺开多 Agent / 高并发架构。
2. **生产愿景与 MVP 分离**：sharding、ES、read replica、partition 等列入「未来」，MVP 不实现，只在数据模型上不堵死。
3. **LLM 只做该做的事**：确定性计算（利润、聚合）用 SQL/Python，LLM 只负责分类、文案、总结，且输出必须 schema 校验。
4. **LLM = Claude**：评论分类用 Haiku 4.5；文案/分析/报告总结用 Sonnet 4.6，复杂场景 Opus 4.8。
5. **仓库分 backend/frontend**；本地开发 MySQL 与 Redis 装在本机运行，后端 uvicorn/celery 本地跑，Docker 容器化留待 VM 部署。
6. **分阶段流程（Phase 3 起）**：每阶段先写 `PHASE_<n>_PLAN.md`（含「为什么这样跑/这样 build」的理由），经用户 + Codex 双重过审后才写代码。

---

## 1. MVP 范围（收敛版）

**MVP 做：**
- 多租户基础：user / account 与 shop 的归属关系
- CRUD：shops / products / orders(+order_items) / reviews
- 评论导入（手动/批量）
- **Review Classifier Agent**（唯一进 MVP 的 LLM Agent，价值清晰、输入输出结构化）
- 利润计算（确定性，非 LLM）
- 基础每日报告（SQL 聚合 + 模板，LLM 仅总结重点）
- 异步任务系统（Celery）+ 任务状态查询（读 MySQL）

**MVP 不做（列入未来阶段）：**
- 真实 Shopee/TikTok/Lazada API 对接
- Listing Writer / Product Analysis / Reply Generator 自动发布
- ES 搜索、sharding、read replica、partition table
- 复杂多 Agent workflow / 编排

---

## 2. 系统架构

```
              ┌─────────────┐
   Client ──► │   FastAPI    │ ──► MySQL 8   (业务数据/事务/任务状态)
 (React后期)  │  (API 层)    │ ──► Redis     (Celery broker/backend；缓存/锁按需加)
              └──────┬───────┘
                     │ enqueue task
                     ▼
              Redis (Celery Broker + 短期 Result)
                     │
                     ▼
              ┌──────────────┐
   队列隔离:   │ Celery Worker │ ──► Agent 模块(worker 内) ──► LLM API (Claude)
 default/llm/  │  (异步任务)   │ ──► MySQL (任务最终状态/结果落库)
 sync          └──────────────┘
              Celery Beat (定时：每日报告)
```

- **Agent 定位明确**：MVP 阶段 Agent 是 **Celery worker 内部的 Python 模块**，不拆独立服务。
- **Redis 职责收敛**：MVP 只做 Celery broker/backend；缓存、限流、分布式锁按需逐步加，不一次全上。
- **任务状态以 MySQL 为准**：`agent_tasks` 表保存最终状态/结果；Redis result backend 只做短期。`GET /agent/tasks/{id}` 读 MySQL。
- **健康检查分级**：`/health`（API 存活）、`/ready`（DB + Redis 可连）。

---

## 3. 数据库设计（v2）

### 关系
```
accounts (1) ─< (N) shops
shops (1) ─< (N) products
shops (1) ─< (N) orders ─< (N) order_items >─ (N..1) products
shops/products/orders (1) ─< (N) reviews    (order_id 可空)
shops (1) ─< (N) shop_daily_metrics
products (1) ─< (N) product_daily_metrics
agent_tasks ─ 关联 shop_id + entity_type/entity_id（弱关联，无外键）
```

### 关键表与字段要点
- **accounts / users**：多租户归属，所有业务表挂 `account_id`（或经 shop 关联），避免后期权限返工。
- **shops**：`platform, shop_name, external_shop_id, status, created_at, updated_at`。
- **products**：`shop_id, product_name, sku, category, price(DECIMAL), cost(DECIMAL), stock, status, external_id, synced_at`。
- **orders**：订单头 `shop_id, order_no, order_status, total_amount(DECIMAL), shipping_fee, platform_fee, ad_cost, created_at, external_id, raw_payload(JSON), synced_at`。
- **order_items**（新增）：`order_id, product_id, quantity, sale_price(DECIMAL), profit(DECIMAL)` —— 支持一单多商品；若 MVP 简化为一单一品，也在此表落地并注明假设。
- **reviews**：`shop_id, product_id, order_id(NULL able), buyer_name, rating, review_text, review_type, sentiment, created_at`。
- **shop_daily_metrics**（Phase 7 实际）：`shop_id, date, orders, revenue, profit, ad_spend, items_total, profit_items_covered, source_task_id, views(NULL), clicks(NULL), conversion_rate(NULL)`。`items_total/profit_items_covered` 为**明细级利润覆盖**口径（未覆盖明细不当 0）；`views/clicks/conversion_rate` 暂无数据源留 NULL（不假造）；`source_task_id` 用于并发 force 下「新任务赢」守卫。
- **product_daily_metrics**（Phase 7 实际）：`product_id, shop_id, date, units_sold, revenue, profit`。重算时同事务**先按 (shop,date) 删旧再插当日全集**，杜绝陈旧商品行残留。
- **daily_reports**（Phase 7 新增）：`shop_id, date, daily_summary(TEXT,LLM), recommended_actions(JSON,LLM), sections_json(JSON,确定性), model, prompt_version, source_task_id, generated_at`。**数字/排名全部在确定性层产出存 `sections_json`，LLM 仅写 `daily_summary/recommended_actions` 两个散文字段**；与数字指标分表（生命周期不同）。
- **agent_tasks**（Phase 3 实际）：`id, idempotency_key(uniq), celery_task_id, task_type, queue_name, status(枚举), shop_id(可空,FK SET NULL), entity_type, entity_id, retry_count, max_retries, input_json, output_json, error_type, error_message, error_detail, model, prompt_version, token_usage, cost(DECIMAL 12,6), duration_ms, started_at, finished_at, created_at, updated_at`。

### 金额 & 精度
- 所有金额字段一律 `DECIMAL(12,2)`（或按币种定标度），**禁用 float**。

### 唯一约束 & 索引
| 表 | 约束/索引 |
| --- | --- |
| products | `uniq(shop_id, sku)`、`(shop_id, status)` |
| orders | `uniq(platform, order_no)` 或 `uniq(shop_id, order_no)`、`(shop_id, created_at)` |
| order_items | `(order_id)`、`(product_id)` |
| reviews | `(product_id, created_at)`、`(shop_id, review_type)`、`(sentiment)` |
| product_daily_metrics | `uniq(product_id, date)`、`(shop_id, date)`、FK `shop_id→shops`、`product_id→products` |
| shop_daily_metrics | `uniq(shop_id, date)`、`(date)`、FK `shop_id→shops` |
| daily_reports | `uniq(shop_id, date)`、FK `shop_id→shops` |
| agent_tasks | `uniq(idempotency_key)`、`(status, task_type)`、`(shop_id, created_at)`、`(celery_task_id)`、FK `shop_id→shops(SET NULL)` |

> **rowcount 语义**：引擎启用 `CLIENT_FOUND_ROWS`，UPDATE 的 `rowcount` 反映「匹配行数」而非「实际改变行数」——使条件门禁（claim/mark/回写）与重算写入相同值时的判断正确。

### 分页
- 列表查询用**游标/keyset 分页**（基于 `id` 或 `created_at`），避免深 offset 性能问题。
- 未来扩展：read replica、partition（按 date）、ES —— MVP 仅预留字段与索引，不实现。

---

## 4. Celery 任务系统（v2）

- **队列隔离**：`default`（轻任务）、`llm`（Agent/LLM 调用）、`sync`（数据导入）。
- **幂等**：每个任务有幂等 key（如 `task_type + entity_id + 业务日期`）或唯一约束，重复投递不产生重复结果。
- **重试/超时**：明确可重试异常、最大次数、指数退避；超时阈值；失败进死信/标记 `failed` 并可查。
- **状态落库**：任务最终状态/结果写 `agent_tasks`（MySQL），不依赖 Redis result backend 长期保存。
- **限流/并发**：对 LLM API 做限流（worker 并发 + 速率限制），防止扩容打爆外部服务或成本。
- **Beat**：MVP 单实例调度每日报告；多实例时需防重复调度（设计中注明，MVP 不实现锁）。

**实际任务类型（task_type → Celery 任务 / 队列）：**
| task_type | Celery 任务 | 队列 | 说明 |
| --- | --- | --- | --- |
| `review.classify` | `app.tasks.llm.classify_review` | llm | 单条评论分类（Phase 4） |
| `review.classify_batch` | `app.tasks.llm.classify_reviews_batch` | llm | 批量分类 + 限流（Phase 5） |
| `profit.compute` | `app.tasks.profit.compute_profit` | default | 确定性利润回写（Phase 6，非 LLM） |
| `daily.report` | `app.tasks.report.generate_daily_report` | llm | 日报：确定性聚合 + LLM 总结（Phase 7） |
| （派发） | `app.tasks.report.dispatch_daily_reports` | default | Beat 每日派发，纯 fan-out 不调 LLM |
| （系统） | `app.tasks.maintenance.reap` | default | 孤儿/卡死任务回收 |

- **队列路由按「具体任务名」**配置（非模块前缀一刀切）：`generate_daily_report→llm`、`dispatch_daily_reports→default`。`llm` 队列任务按 worker 进程维度限流（如批量分类 `rate_limit`）。
- **Beat 实际**：每日 02:00 UTC 跑 `dispatch_daily_reports`，为「昨天(UTC)」逐店建 `daily.report`；幂等键 + upsert + `source_task_id` 守卫兜底重复调度。
- worker 启动需 `-Q default,llm,sync` 同时消费三队列；Beat 另起 `celery ... beat`。

---

## 5. Agent Framework（v2）

**BaseAgent 契约（强化）：**
- `input_schema` / `output_schema`（Pydantic，强制校验）
- `model`、`prompt_version`（可复现）
- `timeout`、`retry` 策略
- `run()` / `validate_input()` / `handle_error()` / `save_result()`
- 可观测性：记录 `token_usage / cost / duration_ms` 到 `agent_tasks`
- LLM 输出**必须** Pydantic 校验，失败时保存原始输出 + 错误，不直接信任

**各 Agent 定位与实现状态：**
- **Review Classifier（✅ 已实现，Haiku 4.5）**：单条（Phase 4）+ 批量（Phase 5）。批量用 **batch prompt**（一次多条、按本批 index 对齐校验），fan-out 切块到 `llm` 队列并发 + `rate_limit` 限流；行级并发冲突按 skipped 处理（不毒性重试）。绝不一条一调。
- **Profit（✅ 已实现，确定性非 LLM，Phase 6）**：毛利/净利/利润率/盈亏平衡价用 `Decimal` 计算；订单费用按收入占比 + 最大余数法分摊（tie-break `order_item_id ASC`），缺成本明细 `profit=NULL` 计入覆盖缺口、不当 0、不转嫁。`POST /agent/profit-analysis` + `GET /analytics/product/{id}/profit`。
- **Daily Report（✅ 已实现，Phase 7）**：确定性 SQL 聚合产出全部数字/排名/计数（存 `daily_reports.sections_json`）；`claude-sonnet-4-6` **仅写散文** `daily_summary/recommended_actions`，不产/复述数字。营收/订单/销量不带 profit 过滤、利润仅 Σ 非空明细；日期半开区间。Beat 定时 + `POST /agent/daily-report` + `GET /analytics/shop/{id}/daily|report`。
- **Reply Generator（未来）**：只生成草稿，需人工确认/置信度阈值，投诉类禁止自动发送。
- **Listing Writer / Product Analysis（未来）**：暂不进 MVP，避免变成「LLM demo 集合」。

**Prompt 管理**：prompt 带版本号，集中管理，便于复现与回归。
**成本控制**：每店铺/每天 token 预算、任务上限、模型 fallback、失败熔断（设计预留，MVP 至少记录成本）。

---

## 6. 项目目录结构

```
backend/
  app/
    main.py
    core/{config,db,redis,celery_app,logging,security}.py
    models/            # SQLAlchemy ORM（含 order_items / *_daily_metrics）
    schemas/           # Pydantic（含各 Agent input/output schema）
    services/          # 业务逻辑（CRUD 薄则直用 session；利润/日报等确定性逻辑在此）
    api/{deps.py, v1/}
    agents/{base,review_classifier,profit_analysis,daily_report,llm}.py
    tasks/             # Celery tasks（按 default/llm/sync 队列组织）
    utils/
  tests/{unit,integration}/
  alembic/
  Dockerfile  .env.example  pyproject.toml  README.md
frontend/              # React 前端（后续阶段开发）
```
> Repository Pattern 按需使用：CRUD 很薄时 Service 直接用 SQLAlchemy session，不为模式而模式。
> 本地 MySQL/Redis：开发期直接连本机，不再用 docker-compose 起容器。

---

## 7. 开发步骤（v2，已调整顺序）

> 注：原计划用 Docker Compose 跑 MySQL/Redis，后改为**本地安装 MySQL/Redis + 后端 uvicorn/celery 本地运行**；Docker 容器化留待 VM 部署阶段。

| Phase | 内容 | 验收 |
| --- | --- | --- |
| 1 | 项目骨架 + 本地 MySQL/Redis/FastAPI；Alembic 可迁移 | `/health` 200、`/ready` 通过、迁移可跑、.env.example 完整 |
| 2 | 多租户 + CRUD（shop/product/order+order_items/review）+ keyset 分页 | REST API 可用、数据闭环 |
| 3 | Celery 异步系统（队列隔离/幂等/重试/超时/状态落库） | 任务入队、状态查 MySQL、失败可查 |
| 4 | Agent Framework（BaseAgent + schema 校验 + 成本记录），只挂 1 个真实 Agent | 示例 Agent 跑通并落库 |
| 5 | Review Classifier Agent（batch、限流） | 批量分类，吞吐与限流验证 |
| 6 | Profit 计算（确定性）+ 接入 CRUD/报告 | 利润字段正确 |
| 7 | Daily Report（SQL 聚合 + 模板 + LLM 总结，Beat 定时） | 每日报告生成 |
| 8（未来） | 高并发优化、平台 API、ES、read replica 等 | 按需 |

每阶段结束 → Codex review → 通过 → 下一阶段。

---

## 8. 第一阶段（Phase 1）已交付文件（位于 backend/）

1. `Dockerfile`（Python 3.12，留待 VM 容器化部署）
2. `pyproject.toml`（fastapi, uvicorn, sqlalchemy, alembic, pydantic-settings, redis, celery, pymysql, anthropic）
3. `.env.example`（本地 DB/Redis/Anthropic key 占位）
4. `app/main.py`（FastAPI + `/health` + `/ready`）
5. `app/core/config.py`（Pydantic Settings）
6. `app/core/db.py`（engine/session/连接池）
7. `app/core/redis.py`
8. `alembic/` + `alembic.ini`（可运行的初始迁移）
9. `README.md`（本地 venv + uvicorn 启动说明）

**Phase 1+2 验收**：在 `backend/` 下 `uvicorn app.main:app` 起得来、`/health` 200、`/ready` 确认本地 MySQL+Redis 可连、`alembic upgrade head` 成功。

---

## 9. 固化清单（Codex 第 2 轮遗留项）

**Phase 2 已落实（Codex 复审 APPROVED，2026-06-19）：**
1. ✅ 金额字段全部 `DECIMAL(12,2)`：`price / cost / sale_price / profit / total_amount / shipping_fee / platform_fee / ad_cost`。
2. ✅ 多租户硬约束：所有业务查询经 `account_id -> shop_id` 过滤；review 的 product_id/order_id 也校验属本店铺。
3. ✅ 订单唯一键 `uniq(shop_id, order_no)`。
4. ✅ `order_items.product_id` 允许 NULL + 快照字段 `external_product_id / sku_snapshot / product_name_snapshot`。
7. ✅ Alembic 干净初始基线 `0001_initial.py`。

**Phase 3 已落实（Codex 复审 APPROVED，2026-06-19）：**
5. ✅ Celery 幂等落成唯一约束：`agent_tasks.idempotency_key`（IntegrityError → re-read）。
6. ✅ `agent_tasks.status` 枚举 `pending/running/succeeded/failed/retry/cancelled`；重试复用同记录、达 max_retries 终态 failed；条件门禁（仅 pending/retry→running、仅 running→succeeded 等）+ 孤儿回收（reaper）。

## 10. 进度

- ✅ Phase 1：项目骨架（APPROVED）
- ✅ Phase 2：多租户 + CRUD + order_items + keyset 分页（APPROVED）
- ✅ Phase 3：Celery 异步任务系统 + agent_tasks（APPROVED，方案 PHASE_3_PLAN.md）
- ✅ Phase 4：Agent Framework + 单条评论分类 Agent（APPROVED，方案 PHASE_4_PLAN.md）
- ✅ Phase 5：Review Classifier 批量化（batch + fan-out + llm 限流，APPROVED，方案 PHASE_5_PLAN.md）
- ✅ Phase 6：Profit 计算（确定性，非 LLM）+ 商品利润 analytics（方案 + 代码均 APPROVED，方案 PHASE_6_PLAN.md）
- ✅ Phase 7：Daily Report（SQL 聚合 + 确定性指标 + LLM 仅总结 + Beat 定时）（方案 + 代码均 APPROVED，方案 PHASE_7_PLAN.md）
- ✅ Phase 8：高并发优化（索引 0004 体检 / API 限流 Redis 固定窗口 / 分布式锁 / 批量导入原子化 / 池配置）（方案 + 代码均 APPROVED，方案 PHASE_8_PLAN.md）
- ✅ Phase 9：Excel 数据导入（异步 sync 队列 + upsert/append-only + reviews 断点续跑 + orders 相邻预扫描 + agent_tasks.account_id 授权）（方案 + 代码均 APPROVED，方案 PHASE_9_PLAN.md）
- ⏭️ 后续（未来）：平台 API（Shopee/TikTok/Lazada）、ES、read replica、partition；其余 Agent（Product Analysis / Listing Writer / Reply Generator）；前端 React；Docker/VM(Xterminal) 部署、Jenkins CI

> MVP 后端 Agent 流水线（评论分类 / 利润 / 日报）已打通；高并发优化（Phase 8）与 Excel 数据导入（Phase 9）已落地；详见 backend/README.md 与各 PHASE_*_PLAN.md。
