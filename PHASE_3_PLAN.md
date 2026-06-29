# Phase 3 方案：Celery 异步任务系统

> **状态：待用户过审 + Codex 过审。两者通过前不写代码。**
> 规则见 CLAUDE.md / CODEX_REVIEW_POLICY.md。设计总纲见 DESIGN.md。

---

## 1. 本阶段做什么

在 backend 上引入异步任务系统（Celery + 本地 Redis），并建立任务的可观测落库机制。这是后续所有 Agent（评论分类、利润、日报）的运行底座。

范围（仅基础设施，不含具体 Agent 业务逻辑——那是 Phase 4+）：

1. **Celery 应用**：`app/core/celery_app.py`，用本地 Redis 作 broker + result backend（短期）。
2. **队列隔离**：`default` / `llm` / `sync` 三条队列。
3. **`agent_tasks` 表**（新增迁移 `0002`）：任务状态/审计落 MySQL，含 Phase 2 遗留的第 5、6 条固化项。字段：
   - 标识：`id`、`idempotency_key`（唯一约束，做幂等）、`celery_task_id`（可空，入队后回填）
   - 归属：`task_type`、`queue_name`、`shop_id`（**可空**——支持非店铺级任务；非空时按租户校验/查询）、`entity_type`、`entity_id`
   - 状态：`status` 枚举 `pending / running / succeeded / failed / retry / cancelled`
   - 重试：`retry_count`、`max_retries`
   - 入出参：`input_json`、`output_json`
   - 错误：`error_type`（异常类名/错误码）、`error_message`、`error_detail`（可空，traceback 摘要）
   - LLM/成本：`model`、`prompt_version`、`token_usage`、`cost(DECIMAL)`
   - 计时：`duration_ms`、`started_at`、`finished_at`、`created_at`、`updated_at`
4. **任务基类/装饰器**：统一任务生命周期，业务任务只写核心逻辑。**明确的所有权边界（见 §2a）**。
5. **一个示范任务**（`ping`/`noop` 或一个最简同步任务）打通链路，验证可观测性，不引入 LLM。
6. **任务状态查询 API**：`GET /api/v1/agent/tasks/{id}`，**读 MySQL**（不依赖 Redis result backend）。
7. **配置**：worker/beat 启动命令、并发与限流参数（写进 README）。

涉及文件（预估）：
- 新增：`app/core/celery_app.py`、`app/models/agent_task.py`、`app/tasks/__init__.py`、`app/tasks/base.py`、`app/tasks/sample.py`、`app/services/agent_task.py`、`app/api/v1/agent_tasks.py`、`alembic/versions/0002_agent_tasks.py`
- 改动：`app/models/__init__.py`（注册新模型）、`app/api/v1/__init__.py`（挂路由）、`pyproject.toml`（已含 celery）、`backend/README.md`（启动说明）

---

## 2a. 任务生命周期与所有权（关键，避免 task id 对不上）

明确职责，避免「Celery task id 与业务 task id 对不上 / 入队失败无记录 / 重复投递难处理」：

1. **API（同步）先建记录再入队**：收到请求 → 在 `agent_tasks` 写一条 `status=pending` 记录（拿到业务 `id`，并按 `idempotency_key` 去重）→ 入队 Celery，把返回的 `celery_task_id` 回填到该记录 → 立即把业务 `id` 返回给调用方。
   - 若 `idempotency_key` 已存在未结束的任务：不重复入队，直接返回已有任务 `id`。
   - 入队若失败：该 `pending` 记录置为 `failed` 并记 `error_*`，调用方可见，不会出现"无记录"。
2. **Worker 只更新这条记录**（不新建），且**用条件更新做状态门禁**：
   - 进入执行前，用**同一条原子 UPDATE** 把状态从 `pending`/`retry` 置为 `running` **并同时写 `started_at`（租约起点）**（`UPDATE ... SET status='running', started_at=now() WHERE id=? AND status IN ('pending','retry')`）。
   - **若该条件更新影响行数为 0**（说明已是 `cancelled`/`succeeded`/终态 `failed`，或已被其他 worker 取走）→ **直接跳过，不执行**。这同时解决了「`acks_late` 下 worker 崩溃导致消息重投、任务被重复执行」的问题——终态任务不会被二次执行。
   - 成功置 `succeeded`+`output_json`+`finished_at`+`duration_ms`；异常置 `failed`/`retry`+`error_*`+`retry_count++`。
3. **业务 `id` 是对外唯一句柄**，`celery_task_id` 仅内部用；状态查询 API 用业务 `id` 读 MySQL。

### 重试 / 取消策略（落实 DESIGN 第 9 节"失败后是否允许重跑"）

- **重试复用同一条 `agent_tasks` 记录**（不新建），`idempotency_key` 保持不变；`retry_count` 递增，达 `max_retries` 后终态 `failed`。
- 可重试状态：`failed`（未达上限）；自动重试由 Celery 退避触发，期间状态标 `retry`。
- 可取消状态：`pending` / `retry`（尚未或等待执行）可置 `cancelled`；`running` 不强杀（MVP 不做抢占）。
- **取消生效靠 worker 的状态门禁**：即便取消时消息已在队列里，worker 执行前的条件 UPDATE（仅 `pending/retry -> running`）会因状态已是 `cancelled` 而影响 0 行，从而跳过，不会执行已取消的任务。
- 终态：`succeeded` / `cancelled` / 达上限的 `failed` —— 不再自动流转。
- 手动重跑终态任务：以**新 `idempotency_key`** 新建任务（视为新的业务意图），避免与历史审计混淆。

### 并发与孤儿任务（MVP 处理）

- **`idempotency_key` 并发竞争**：用**唯一索引**兜底；API 创建 pending 时若并发请求撞键 → 捕获 `IntegrityError` → 回滚后按该 key **re-read 已有记录并返回其 `id`**，保证同一 key 只入队一次。
- **孤儿 `pending`**（API 建记录后、入队/回填前进程崩溃）：超过阈值（如 5 分钟）仍 `pending` 且无 `celery_task_id` 的记录，重新入队或标 `failed`。
- **卡死 `running`**（`acks_late` 下 worker 置 `running` 后崩溃，消息重投又被条件门禁挡掉 → 任务永久卡 `running`）：worker 置 `running` 时记 `started_at`（作为租约起点）；扫描任务回收**`running` 且 `started_at` 早于超时阈值**（如 > `task_time_limit` + 缓冲）的记录 —— 按 `retry_count`/`max_retries` 决定**标 `retry` 重入队**或终态 `failed`。这样重投崩溃的任务不会卡死。
- 上述回收统一由一个**扫描任务/脚本**（MVP 先提供手动触发 + 标记可见；Celery Beat 定时自动回收放后续）覆盖 `pending` 与 `running` 两类孤儿。
- **回收重入队必须同步递增 `retry_count`** 并受 `max_retries` 约束，否则反复崩溃会无限回收。

## 2. 为什么用这种逻辑跑（运行 / 数据流 / 编排的理由）

**为什么要异步任务系统，而不是在 API 里直接做？**
Agent 任务（调 LLM、批量分类上万条评论、生成日报）耗时从数百毫秒到数分钟不等。若放在 HTTP 请求里同步执行：请求会超时、Web 进程被长任务占满、无法重试、无法限流。把重活丢给 Celery worker 异步执行，API 只负责「建记录入队 + 返回业务任务 id」（非 Celery 的 task_id），前端轮询状态——这是生产级后端的标准做法。

**为什么 broker 用本地 Redis？**
项目技术栈已定 Redis，且 Celery 对 Redis broker 支持成熟、本地零额外组件。MVP 阶段本机一个 Redis 同时做 broker 与（短期）result backend 足够。

**为什么任务状态以 MySQL 为准，而不是 Redis result backend？**
Redis result backend 适合临时存放、会过期，且大量 `agent_tasks/day` 长期堆在 Redis 不合适（Codex 第 1 轮也指出过）。业务需要「按店铺/商品/时间追溯任务、排障、算成本」，这些必须是持久、可查询的关系数据——所以最终状态写 `agent_tasks` 表，状态查询 API 读 MySQL。Redis 只承担「队列 + 短期运行态」。

**为什么要队列隔离（default/llm/sync）？**
不同任务特征差异大：LLM 任务慢且受外部速率限制，数据同步任务 IO 重，轻任务要求快。混在一条队列里，慢任务会饿死快任务。分队列后可分别设并发与限流，互不拖累，将来也能按队列独立扩 worker。

**为什么要幂等（idempotency_key）？**
异步系统里重复投递、重试、用户重复点击都会导致同一任务跑多次。对「给某商品生成日报」「分类某批评论」这类操作，重复执行会产生重复数据或重复扣费（LLM）。用 `idempotency_key` 唯一约束，让同一逻辑任务只生效一次。

**为什么显式 status 枚举 + 重试/超时？**
任务会失败（网络、LLM 限流、超时）。明确的状态机（pending→running→succeeded/failed/retry/cancelled）让失败可观测、可重跑、可告警；超时防止任务卡死占用 worker。

---

## 3. 为什么要这样 build 代码（结构 / 取舍 / 权衡的理由）

**为什么用「任务基类/装饰器」统一生命周期？**
每个任务都要做同样的样板：建记录、置 running、捕获异常、落结果/错误、记录耗时与成本、按规则重试。把这套抽到 `tasks/base.py`，业务任务只写核心逻辑，既减少重复，又保证所有任务的可观测字段一致（否则各写各的，排障时字段缺失）。这呼应 DESIGN 里 BaseAgent 的契约思路，但这一层只管「任务执行框架」，不含 LLM。

**为什么先做一个不含 LLM 的示范任务？**
Phase 3 的目标是把「入队 → 执行 → 落库 → 查询」整条链路和可观测性打通。先用一个最简任务验证框架本身正确，再在 Phase 4/5 往里填真正的 Agent，能把「框架 bug」和「Agent 逻辑 bug」分开排查，降低风险。

**为什么 `agent_tasks` 不设业务外键、只用弱关联（shop_id + entity_type/entity_id）？**
任务可能跨多种实体（商品/评论/订单/店铺），且任务记录是审计日志性质，不应随业务数据删除而级联消失。用弱关联保留可追溯性，同时不给业务表加耦合。但 `shop_id` 仍会按租户校验，保证查询隔离。

**为什么 result backend 与状态表分离（两套存储）？**
Celery 自身需要 result backend 才能 join/链式编排任务，这用 Redis；而业务可查询的真相在 MySQL。两者职责不同：Redis 是「运行时机制」，MySQL 是「业务事实」。分离后各自可独立演进（如将来换 broker 不影响业务数据）。

**与现有结构的一致性**：沿用 Phase 2 的分层（api → service → model）与按需 Repository 原则；任务的 DB 访问走 `services/agent_task.py`，路由只做编排，保持和 shops/products 一致的代码风格。

---

## 3a. Celery 配置（显式落地，不停留在描述层）

`app/core/celery_app.py` 将显式设置：

- **broker / backend**：`broker_url = REDIS_URL`，`result_backend = REDIS_URL`；`result_expires = 3600`（结果只短期留 Redis，真相在 MySQL）。
- **队列路由**：`task_routes` 把任务按前缀/名称分到 `default` / `llm` / `sync`；`task_default_queue = "default"`。
- **超时**：`task_time_limit`（硬，如 600s）、`task_soft_time_limit`（软，如 540s，软超时抛异常走 failed/重试）。
- **可靠投递**：`task_acks_late = True`（任务执行完才 ack，避免 worker 崩溃丢任务）、`worker_prefetch_multiplier = 1`（慢任务场景防止预取饿死）。
- **重试**：任务级 `autoretry_for`、`retry_backoff = True`、`retry_backoff_max`、`retry_jitter = True`、`max_retries`（与 `agent_tasks.max_retries` 一致）。
- **序列化**：`task_serializer = "json"`、`accept_content = ["json"]`。
- **限流**：`llm` 队列任务设 `rate_limit`（为 Phase 5 LLM 限流预留；本阶段配置占位即可）。

## 4. 验收标准（Phase 3）

- 启动 worker（`celery -A app.core.celery_app worker -Q default,llm,sync`）成功。
- 通过 API 或脚本触发示范任务，`agent_tasks` 表出现记录，状态从 pending→running→succeeded，并记录 `duration_ms`。
- `GET /api/v1/agent/tasks/{id}` 从 MySQL 读回状态。
- 同一 `idempotency_key` 重复投递不产生第二条成功任务。
- 模拟失败时状态落为 `failed` 并记录 `error_message`，按配置重试。
- `alembic upgrade head` 应用 `0002` 成功；与 ORM 一致。

---

## 5. 明确不在本阶段做（留待后续）

- 任何真实 LLM 调用与具体 Agent 业务（Phase 4 起）。
- 评论批量分类的吞吐优化（Phase 5）。
- Celery Beat 定时（日报，Phase 7）——本阶段可预留配置但不实现定时业务。
- 多实例 Beat 防重复调度（生产部署阶段）。
