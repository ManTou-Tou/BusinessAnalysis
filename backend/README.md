# AI E-Commerce Copilot — Backend

面向 Shopee / TikTok Shop / Lazada 卖家的 Agent 驱动电商运营后端。

需求见 [../project.md](../project.md)，设计见 [../DESIGN.md](../DESIGN.md)。
**规则：所有方案/代码须经 Codex review 通过后才采用（见 [../CODEX_REVIEW_POLICY.md](../CODEX_REVIEW_POLICY.md)）。**

## 技术栈

Python 3.12 · FastAPI · SQLAlchemy · Alembic · 本地 MySQL 8 · 本地 Redis · Celery · LLM = Claude

> 本地开发：MySQL 与 Redis 直接装在本机（不再用 Docker 容器跑）。后端用 uvicorn/celery 本地运行。
> `Dockerfile` 保留供后续在虚拟机（Xterminal）上做容器化部署。

## 当前阶段：Phase 7（Daily Report）—— 方案 Codex APPROVED，代码已实现

> 各阶段权威进度以 [../DESIGN.md](../DESIGN.md) §10 为准。下面按阶段记录已交付内容。

### Phase 2：多租户 + CRUD（已通过 Codex review）

- 多租户：account → shop 归属；业务请求用 `X-Account-Id` 头标识租户，所有查询按 account 隔离。
- CRUD：shops / products / orders(+order_items) / reviews（批量导入）。
- keyset（游标）分页：列表用 `?cursor=&limit=`，避免深 offset。
- 干净初始迁移 `alembic/versions/0001_initial.py`（金额 DECIMAL、`uniq(shop_id, order_no)`、order_items 快照字段）。

> 评论分类字段（review_type/sentiment）在 Phase 5 由 Agent 填充。

### Phase 3：Celery 异步任务系统（已实现，方案见 ../PHASE_3_PLAN.md）

任务最终状态以 MySQL `agent_tasks` 为准（Redis 仅作 broker + 短期 result）；队列隔离 `default/llm/sync`；
幂等（`idempotency_key` 唯一）、重试/超时、条件门禁（仅 `pending/retry->running`）、孤儿回收。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/agent/tasks/{id}` | 查询任务状态（读 MySQL）；`{id}` 为**业务任务 id**，非 Celery task_id |
| POST | `/agent/tasks/{id}/cancel` | 取消任务（仅 pending/retry 可取消） |
| POST | `/agent/tasks/sample` | dev 用：触发示范任务（body: `shop_id`,`idempotency_key?`,`payload?`） |
| POST | `/agent/tasks/maintenance/reap` | dev 用：手动回收孤儿/卡死任务 |

启动 worker（另开一个终端，在 `backend/` 下，需 Redis 已运行）：
```powershell
celery -A app.core.celery_app:celery_app worker -Q default,llm,sync --loglevel=info
```

### Phase 4：Agent Framework + 评论分类 Agent（已实现，方案见 ../PHASE_4_PLAN.md）

第一个真实 Claude Agent：单条评论分类。需在 `.env` 配 `ANTHROPIC_API_KEY`（真实调用 Claude，按量计费）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/agent/review-classifier` | 触发单条评论分类（body: `review_id`,`force?`）；走 `llm` 队列异步执行 |

- 成功后把 `review_type / sentiment` 写回 `reviews`，并把 `model / prompt_version / token_usage / cost` 记入 `agent_tasks`（同一事务）。
- 分类模型默认 `claude-haiku-4-5-20251001`（`LLM_MODEL_CLASSIFY` 可配）。
- 状态查询仍走 `GET /agent/tasks/{id}`。
- 批量 / 限流 / 高吞吐留待 Phase 5。

> 结构化输出用 `anthropic` SDK 的 `messages.parse(output_format=PydanticModel)`，需较新版本的 SDK；`pip install -e .` 会安装最新版。若环境里 SDK 过旧报 `AttributeError`，升级：`pip install -U anthropic`。

### Phase 5：批量评论分类（已实现，方案见 ../PHASE_5_PLAN.md）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/reviews/classify` | 批量分类（body: `shop_id`,`limit?`,`only_unclassified?`=true,`force?`=false）；切块 fan-out 到 `llm` 队列 |

- 返回 `{enqueued_chunks, enqueued_reviews, remaining}`；`remaining` 为符合条件但本次未入队数（受单次上限 `LLM_CLASSIFY_MAX_PER_REQUEST` 约束，不静默截断）。
- 一次 LLM 调用分类一块（`LLM_CLASSIFY_CHUNK_SIZE`，默认 25）；`llm` 队列任务限流 `LLM_CLASSIFY_RATE_LIMIT`（默认 `60/m`，**每 worker 进程**维度）。
- 默认只分类未分类评论；`force=true` 重新分类并覆盖。并发冲突/已删评论计入任务 `output_json.skipped`，不致整块失败。
- 吞吐取决于 Anthropic 账号档位与 worker 数；提高吞吐可加 worker 或 `--concurrency`，并相应调 `LLM_CLASSIFY_RATE_LIMIT`。

### Phase 6：Profit 计算（确定性，非 LLM）（已实现，方案见 ../PHASE_6_PLAN.md，Codex APPROVED）

利润计算**全程不调 LLM**（DESIGN §3.18）：逐 `order_items` 算毛利/净利/利润率/盈亏平衡价。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/agent/profit-analysis` | 触发利润计算（body: `shop_id`,`order_id?`,`force?`=false）；走 `default` 队列异步执行 |
| GET | `/analytics/product/{product_id}/profit` | 同步只读：该商品利润聚合 |

- 计算口径：`item_fee` 按全体明细收入占比分摊（最大余数法，tie-break `order_item_id ASC`），二分为
  `allocated_fee`(有成本) / `unallocated_fee`(缺成本)；守恒 `Σallocated + Σunallocated == 订单费用`。
- 成本缺失（`product_id` 为空/商品无成本）的明细 `profit` 置 `NULL` 并计入任务 `output_json.skipped_no_cost`/`unallocated_fee`，
  analytics 聚合排除并报 `excluded_no_cost`；`_finalize` 会**强制清**这些明细的旧 `profit`（防虚高）。
- 成功后把每条 `order_items.profit`（净利）回写 + 汇总记入 `agent_tasks.output_json`（同一事务）。
- 幂等：默认 `force=false` 命中同键复用任务；`force=true` 追加 uniquifier 新建任务（改了成本/广告费后重算）。
- 单次最多处理 `PROFIT_MAX_ORDERS_PER_RUN`（默认 1000）张订单，`output_json.remaining_orders` 反映未处理数（不静默截断）。
- 该任务走 `default` 队列：worker 启动命令已含 `default`（见上文 Phase 3 启动命令）。

### Phase 7：Daily Report（确定性聚合 + LLM 仅总结 + Beat）（已实现，方案见 ../PHASE_7_PLAN.md，Codex APPROVED）

先用 SQL 确定性算当日指标并落库，再让 `claude-sonnet-4-6` **仅据既成事实写散文**；数字一律不来自 LLM。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/agent/daily-report` | 触发日报（body: `shop_id`,`date?`=昨天UTC,`force?`）；走 `llm` 队列异步 |
| GET | `/analytics/shop/{shop_id}/daily?date=` | 同步读：店铺某日**数字**指标 |
| GET | `/analytics/shop/{shop_id}/report?date=` | 同步读：店铺某日**报告**（散文 + 确定性 sections） |

- 三表：`shop_daily_metrics` / `product_daily_metrics`（数字）+ `daily_reports`（散文，含确定性 `sections_json`）。迁移 `0003`。
- 队列：`generate_daily_report` → `llm`（含 Sonnet 调用）；`dispatch_daily_reports` → `default`（纯派发）。
- **Beat 定时**：每日 02:00 UTC 跑 `dispatch_daily_reports`，为「昨天」逐店建日报（MVP 单实例；多实例锁延后）。启动 Beat：
  ```powershell
  celery -A app.core.celery_app:celery_app beat --loglevel=info
  ```
- 利润取 `order_items.profit`（Phase 6 产出）：缺利润的明细不当 0，记 `items_total`/`profit_items_covered` 覆盖缺口、报告提示。营收/订单/销量不受 profit 缺失影响。
- 幂等：默认 `force=false` 命中复用；`force=true` 新建并覆盖；`daily_reports` 用 `source_task_id` 守卫防旧任务覆盖新报告。
- `views/clicks/conversion_rate` 暂无数据源 → 留 NULL（不假造）。

### Phase 8：高并发优化（已实现，方案见 ../PHASE_8_PLAN.md，Codex APPROVED）

- **索引体检**（迁移 0004）：补 keyset/批量分类/日报/reaper 热路径的覆盖排序索引（消除 filesort）。
- **批量导入原子化**：`POST /reviews/import` 改 Core 批量插入（单事务 + 分块 + 末尾一次 commit），单次上限 `REVIEW_IMPORT_MAX_PER_REQUEST`（超出 400）。
- **API 限流**：Redis 固定窗口（`INCR`+`PEXPIRE` Lua 原子，fail-open），维度 = 路由 + 租户/IP，超限 429 + `Retry-After`；写/触发端点已挂，`/health`/`/ready`/dev 桩不挂。
- **分布式锁**：`redis_lock`（`SET NX PX` + Lua 比对 token 释放）套到 Beat 日报派发；锁失效靠幂等键兜底（锁提速、幂等保正确）。
- 连接池参数（`DB_POOL_SIZE` 等）走 env。

### Phase 9：Excel 数据导入（已实现，方案见 ../PHASE_9_PLAN.md，Codex APPROVED）

上传 `.xlsx` 异步导入（worker，`sync` 队列）。列模板见 [docs/import_templates.md](docs/import_templates.md)。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/imports/{entity}` | `entity ∈ shops\|products\|orders\|reviews`；multipart `file`，非 shops 需 `shop_id`；返回任务 id |

- **冲突策略**：products/orders/shops **upsert**（按唯一键）；reviews **insert-only**（append，传 `upsert` 报 400）。
- **大文件**：openpyxl 流式读、分块提交、**每块落进度**（`output_json.progress`）；状态/结果/行级错误查 `GET /agent/tasks/{id}`。
- **重试**：reviews **断点续跑**（不重复）；products/orders/shops 从头重跑（幂等）。orders 同 `order_no` 明细须相邻，非相邻 → 整文件失败。
- **任务授权**：`agent_tasks.account_id`（迁移 0006）使 shops 导入（无 shop_id）等账号级任务可被本租户查询。
- 入口校验：扩展名 + ZIP 魔数 + 大小（`IMPORT_MAX_FILE_BYTES`）；成功后删文件、失败/重试中保留。

### API 一览（均挂在 `/api/v1`，业务接口需 `X-Account-Id` 头）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST/GET | `/accounts` | 创建/列出租户（开发桩，仅非生产环境，`APP_ENV=production` 时返回 404） |
| POST/GET | `/shops`、GET `/shops/{id}` | 店铺 |
| POST/GET/PUT | `/products`、`/products/{id}` | 商品（`?shop_id=` 过滤） |
| POST/GET | `/orders`、GET `/orders/{id}` | 订单（含明细 items） |
| POST | `/reviews/import` | 批量导入评论 |
| GET | `/reviews` | 列出评论（`?shop_id=&product_id=`） |

## 本地开发：前置条件

1. 本机安装并**启动** MySQL 8 **与 Redis 服务**（两者都必须在运行，否则 `/ready` 与迁移会失败）。
2. 在 MySQL 中创建数据库与用户（与 `.env` 一致）：
   ```sql
   CREATE DATABASE copilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'copilot'@'localhost' IDENTIFIED BY 'copilot_pass';
   GRANT ALL PRIVILEGES ON copilot.* TO 'copilot'@'localhost';
   FLUSH PRIVILEGES;
   ```

## 快速开始

> 以下命令均需在 **`backend/` 目录下**执行（alembic、uvicorn 都依赖该目录为工作目录）。

```powershell
# 1. 环境变量
copy .env.example .env       # 按需修改密码、ANTHROPIC_API_KEY 等

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .

# 3. 执行数据库迁移
alembic upgrade head

# 4. 启动 API
uvicorn app.main:app --reload --port 8000
```

## Docker（可选，用于后续 VM 部署）

`Dockerfile` 以 **`backend/` 为构建上下文**。从仓库根目录构建时需指定上下文：

```bash
docker build -t copilot-backend backend/
```
（容器内连接本机 MySQL/Redis 时，`MYSQL_HOST`/`REDIS_URL` 需指向宿主机，如 `host.docker.internal`。）

## 健康检查

| 端点 | 说明 |
| --- | --- |
| `GET /health` | 存活探针，进程在跑即 200 |
| `GET /ready`  | 就绪探针，检查本地 MySQL + Redis 连通；任一失败返回 503 |

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

## 数据库迁移（Alembic）

```bash
alembic revision --autogenerate -m "message"   # 生成迁移
alembic upgrade head                            # 应用迁移
```

连接串与目标元数据由应用配置统一提供（见 `alembic/env.py`、`app/core/config.py`），不在 `alembic.ini` 硬编码。

## 验收标准（Phase 1+2）

- `uvicorn` 起得来；`GET /health` 返回 200
- `GET /ready` 确认本地 MySQL + Redis 可连
- `alembic upgrade head` 成功
- 带 `X-Account-Id` 头可完成 shop/product/order/review 的增查，且无法越权访问他人 account 数据
