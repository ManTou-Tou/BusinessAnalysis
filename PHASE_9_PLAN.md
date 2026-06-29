# Phase 9 方案：Excel 数据导入（异步 + Upsert，shops/products/orders/reviews）

> **状态：v3 —— 用户已过审 v1；v2 加断点续跑；v3 整改 Codex 第 1 轮 5 项：①orders 非相邻同 order_no 校验 ②agent_tasks 加 account_id 修复 shops 导入任务授权 ③错误报告按块落 output_json（失败也可查）④Celery 路由精确到 `app.tasks.imports.*` ⑤shops 唯一键迁移含存量去重前检。待 Codex 复审。通过前不得写代码、不得采用。**
>
> **用户确认的硬约束（逐条钉死，勿偏离）：** ① products/orders/shops 用 **upsert**；② reviews **append-only**（暂不去重，接平台 API 拿 `external_review_id` 后再加）；③ 每个导入任务必须有 **error report**；④ 大文件必须支持 **retry**；⑤ 每批 commit 后**记录进度**；⑥ **不一次性把整文件读进内存**（openpyxl 流式）。
> 规则见 CLAUDE.md / CODEX_REVIEW_POLICY.md。需求来源：project.md「第一阶段只做后台系统……可以先手动导入数据」+ 用户明确：支持**全部实体**、冲突时 **Upsert 更新**、**异步走 Celery**。
> 复用底座：Phase 3（agent_tasks 生命周期/幂等/队列）、Phase 8（Core 批量插入/分块）、Phase 2（多租户 account→shop 隔离）。

---

## 0. 目标与边界

让用户上传 **.xlsx** 文件批量导入数据，**先把项目用真实数据跑起来**（无需对接平台 API）。四类实体都支持；大文件不阻塞 API（异步走 `sync` 队列）；重复导同一张表按唯一键 **upsert**（纠错/补数不产生重复）。

**本阶段做：** xlsx 解析 + 校验 + 租户隔离 + 按实体 upsert + 异步任务 + 行级错误报告 + 状态查询。
**不做（后续）：** CSV/JSON 文件格式、列映射 UI、导出、定时自动导入、跨机共享存储（VM 阶段）、reviews 去重外部 id。

---

## 1. 本阶段做什么

### 1.1 依赖（需说明：引入 openpyxl）

解析 .xlsx 需要库。选 **openpyxl**（纯 Python、xlsx 读专用、支持 `read_only=True` 流式逐行、无需 pandas/numpy 重栈）。**这是 Python 生态内的工具库、不改变项目技术栈（仍是 Python 后端）**，按 CLAUDE.md「栈外技术需先说明」在此说明并请 Codex 确认。不选 pandas（重、为读表引入数值栈不值）。加入 `pyproject.toml` 依赖。

### 1.2 上传接口（API 层，轻）

- `POST /api/v1/imports/{entity}`（`entity ∈ shops|products|orders|reviews`），`multipart/form-data`：
  - 文件字段 `file`（.xlsx）；`products|orders|reviews` 需表单字段 `shop_id`（校验归属当前 account）；`shops` 为 account 级、无需 shop_id。
  - 可选 `conflict`（默认 `upsert`；`reviews` 固定 `insert`，见 §3）。
- **入口校验**：扩展名 `.xlsx` + 内容首字节为 ZIP 魔数 `PK\x03\x04`（xlsx 是 zip）；大小 ≤ `import_max_file_bytes`（默认 20MB）超出 413/400。
- **落盘**：保存到配置目录 `import_upload_dir`（默认 `backend/var/imports/`，**不在 web 根**），文件名 = `uuid4().hex + .xlsx`（防路径穿越/重名）。
- **建任务入队**：`agent_tasks(task_type=import.{entity}, queue=sync)`，`input_json={path, account_id, shop_id?, conflict}`；幂等键 = `import.{entity}:{account_id}:{sha256(文件内容)}`（同文件重传复用任务，不重复处理）；`force` 追加 uuid 重跑。返回业务任务 id。
- 状态查询沿用 `GET /api/v1/agent/tasks/{id}`（读 MySQL，含 `output_json` 的导入统计）。

### 1.3 解析 + 导入任务（worker，重）`app/tasks/imports.py`（queue=sync）

经 Phase 3 `run_lifecycle`（queue 路由见 §1.6）。`_run` 自管 `SessionLocal`（沿用 report `_run` 模式），逐块独立提交：
1. **打开** `openpyxl.load_workbook(path, read_only=True, data_only=True)` 取第一个 sheet；`data_only=True` 取公式计算值、不执行公式（安全）。
2. **表头映射**：首行作表头，列名 **小写去空格**后按实体的「列名→字段」字典映射；缺**必填列** → 整文件结构错（标 `failed` + 错误落库，不入数据行）。未知列忽略。
3. **起点（仅 reviews 需要断点续跑）**：见 §1.5 —— reviews 从 `output_json.progress.processed_rows` 续跑（append-only 防重复）；products/orders/shops **从头重跑**（upsert/先删后插幂等，重跑不产生重复，无需游标）。openpyxl `iter_rows` 流式，仅 reviews 跳过 ≤ 已提交偏移的行。
4. **逐块**（流式，块大小 `bulk_insert_chunk_size`，默认 1000；orders 按 order_no 边界切块，§1.4）解析 → 校验 → 入库：
   - 校验：类型（金额 `Decimal`、数量 `int`、rating 1–5）、必填、租户/归属（product/order 属本 shop；外键解析见 §1.4）。**行级错误**带行号收集，最多 `import_max_row_errors`（默认 200）条，其余只计数（不静默吞，总数照报）。
   - 入库：upsert（products/orders/shops）或 insert（reviews），见 §1.4。
   - **每块一个事务**：块内数据写入 **+** 进度/错误（`processed_rows` + 累计 `inserted/updated/skipped/error_count` + 截断后的 `errors[]`）写进 `agent_tasks.output_json` **同一事务 commit**。用新增服务 `update_import_progress(db, task_id, output_json)`——**状态门禁 `status=running` 的 UPDATE，仅改 `output_json`**（不碰 status/error_*），故与 run_lifecycle 的终态写入不冲突。
**错误报告落地（解决 Codex #3）**：因 `output_json` **每块就提交**，无论任务最终 succeeded / failed / retry，最近一次提交的 `output_json` 都含 `{errors, error_count, processed_rows, inserted/updated/skipped}`，`GET /agent/tasks/{id}` 始终可查。`run_lifecycle` 失败路径的 `mark_failed/mark_retry` 只改 status/error_*、**不覆盖 output_json**（现有实现即如此），错误报告不丢。
`_finalize`：写最终汇总 `output_json` + 标记成功；**部分行错误仍算任务完成**（错误在报告里），仅**结构性错误/全量失败**才 `failed`。任务**成功终态后删上传文件**；**失败/重试中保留文件**（reviews 续跑、其它重跑都需要它），达 `max_retries` 终态后再删。

### 1.4 各实体的唯一键与 upsert/插入语义（**关键，逐实体钉死**）

upsert 只在「有天然唯一业务键」时成立。逐实体：

| 实体 | 唯一键 | 语义 | 备注 |
| --- | --- | --- | --- |
| **products** | `uniq(shop_id, sku)`（已存在） | **Upsert**：`INSERT ... ON DUPLICATE KEY UPDATE`（product_name/price/cost/stock/status/category…） | 直接可用 |
| **orders** | `uniq(shop_id, order_no)`（已存在） | **Upsert 订单头** + **明细先删后插**：同事务 `DELETE order_items WHERE order_id=?` 再插该单全部明细（沿用 Phase 7 「先删后插」防陈旧残留） | 一行=一条订单明细，按 `order_no` 分组；订单头字段取该组首行 |
| **shops** | **需新增** `uniq(account_id, external_shop_id)` | **Upsert**（按 external_shop_id） | 见下：迁移 0005 加唯一约束，sheet 必填 `external_shop_id` |
| **reviews** | **无天然唯一键**（评论是事件流） | **仅 Insert（append）**，不 upsert | 见 §3 理由；`conflict` 对 reviews 固定 `insert`，传 `upsert` 报 400 |

- **迁移 `0005`（shops 唯一键，含存量去重前检，解决 Codex #5）**：给 `shops` 加 `uniq(account_id, external_shop_id)`（同步模型 `__table_args__`）。`upgrade()` **先查存量重复**：`SELECT account_id, external_shop_id, COUNT(*) ... WHERE external_shop_id IS NOT NULL GROUP BY 1,2 HAVING COUNT(*)>1`；**若有重复 → 主动 `raise` 报错并列出冲突键**，要求先人工清理，不静默建失败。MySQL 唯一索引允许多 NULL，但 upsert 依赖非空键，故 shops sheet 的 `external_shop_id` **必填非空**，空值整行报错。
- **orders 同 order_no 必须相邻（解决 Codex #1）**：worker 在单次流式遍历中维护「已关闭 order_no」集合；某 order_no 的分组结束后**再次出现** → 判定文件非法（同一订单被拆散会让"先删后插"丢失先前明细）。处理：**整文件失败**（结构错，标 `failed` + 错误指明违例 order_no）。因 orders 重试是**从头整文件重跑**（§1.5），单次遍历的 seen 集合始终完整，检测无盲区。模板要求同订单明细行相邻。
- **外键解析**：orders 明细与 reviews 用 **`sku`/`order_no` 等业务键**解析出 `product_id`/`order_id`（按 shop 内查找）；解析不到 → 该行错误（orders 明细可落快照字段 `sku_snapshot` 等，product_id 置空，沿用现有可空设计）。
- **upsert 用 `mysql_insert(...).on_duplicate_key_update(...)`**（与 Phase 7 写法一致），分块 executemany。

### 1.7 任务授权：agent_tasks 增 account_id（修复 shops 导入查询，解决 Codex #2）

现有 `agent_task.get_for_account` 用 `JOIN shops ON agent_tasks.shop_id=shops.id` 按 account 授权——**shops 导入任务 `shop_id=NULL`，会 join 不上、用户查不到进度**。修复：
- **迁移 `0006`**：`agent_tasks` 加 `account_id BIGINT NULL` + 索引 `(account_id, created_at)`；`upgrade()` **回填**存量：`UPDATE agent_tasks t JOIN shops s ON t.shop_id=s.id SET t.account_id=s.account_id`（旧任务有 shop_id 的都能回填；无 shop_id 的系统任务保持 NULL、本就不对用户暴露）。同步模型 `__table_args__`。
- **`create_task` 增 `account_id` 入参**：所有建任务处（含本阶段 import 与既有 agents/reviews/agent_tasks 触发）显式传 `account_id`（API 已有该值）。**既有触发点一并补传**（小改，保持向后行为：之前靠 shop join，现在直接列）。
- **`get_for_account` 改为按 `AgentTask.account_id == account_id` 授权**（不再依赖 shop join），从而覆盖 shop 级与 account 级（shops 导入）任务；`cancel_for_account` 同。
- 这是对 Phase 3 表的**增量扩展**（只加列 + 改授权来源），不改任务状态机/幂等/reaper 等不变量。

### 1.5 事务与重试策略（**按实体区分，需说明**）

Phase 8 的 `POST /reviews/import`（JSON、小、同步）是请求级原子；本阶段 Excel（异步、大）一律**分块提交**（不把十万行塞单事务，避免长锁/大 undo log），每块数据 + 进度同事务提交。**重试语义按实体幂等性区分**：

| 实体 | 重试方式 | 为什么不重复 |
| --- | --- | --- |
| **reviews** | **断点续跑**：从已提交 `processed_rows` 偏移续，跳过已处理行 | append-only、无唯一键，**不能靠 upsert**；唯一手段是"已提交行不再处理" |
| **products / shops** | **从头整文件重跑** | upsert（ON DUPLICATE KEY）幂等，重复处理同行结果一致 |
| **orders** | **从头整文件重跑** | 订单头 upsert + 明细「先删后插」按订单幂等；从头跑使 order_no 相邻性检测（§1.4）在单次完整遍历内、无盲区 |

- 只有 reviews 需要续跑游标；其它三类**重跑即幂等**，从头跑更简单，且让 orders 的全局相邻性校验完整。把游标限定到 reviews，避免给幂等实体引入跨重试状态。
- 三类都**每块提交进度**（§1.3）供观测；orders **按 order_no 边界切块**，保证一个订单的「先删后插」在单事务内完成、不被拆散。

### 1.6 Celery 路由与注册（精确到任务名，解决 Codex #4）

- 任务模块 `app.tasks.imports`，celery `include` 加入；路由用**模块前缀精确匹配** `app.tasks.imports.* → sync`（现有路由按 Celery 任务名匹配；写 `import.* ` 不会命中，必须用真实任务名前缀）。
- worker 需 `-Q default,llm,sync`（启动命令已含 sync）。
- reaper registry：把 `import.{entity}`（task_type）→ 对应 Celery 任务加入 `maintenance._registry()`，使孤儿可重投（重投后 reviews 续跑、其它重跑）。
- dispatcher 入队失败留可见失败记录（同 Phase 3/5）。

### 涉及文件（预估）
- 新增：`app/api/v1/imports.py`、`app/tasks/imports.py`、`app/services/imports/`（按实体拆 parser+upsert）、`app/schemas/imports.py`、`alembic/versions/0005_shops_unique_external.py`、`alembic/versions/0006_agent_tasks_account_id.py`、`tests/test_import_parsing.py`、`tests/test_import_upsert.py`、`tests/test_import_resume.py`、`docs/import_templates/*.md`
- 改动：`app/api/v1/__init__.py`（挂 router）、`app/core/celery_app.py`（include + 路由 `app.tasks.imports.* → sync`）、`app/core/config.py`（`import_*` 配置）、`app/tasks/maintenance.py`（registry）、`app/models/shop.py`（uniq）、`app/models/agent_task.py`（account_id + 索引）、`app/services/agent_task.py`（`create_task` 增 account_id、`get_for_account`/`cancel_for_account` 改授权、新增 `update_import_progress`）、**既有建任务处补传 account_id**（`api/v1/agents.py`、`api/v1/reviews.py`、`api/v1/agent_tasks.py`、`tasks/report.py` dispatch、`services/review.py` enqueue）、`pyproject.toml`（openpyxl）、`backend/README.md`、`.env.example`

---

## 2. 为什么用这种逻辑跑（运行/数据流）

**为什么异步走 sync 队列？** 十万行解析+入库慢，同步会阻塞 HTTP/超时。沿用 Phase 3「`sync` 队列 = 数据导入」的既定定位，上传只做落盘+建任务（快），重活在 worker，状态查 `agent_tasks`，与现有任务体系一致、可观测、可重试。

**为什么先落盘再传 path，而不是把文件塞进消息？** Celery 消息走 Redis，不该塞 20MB 文件（撑爆 broker）。落盘传路径是标准做法（MVP 单机 API/worker 共享本地盘；VM 跨机共享存储后续）。

**数据流：**
```
POST /imports/{entity}  (file, shop_id?, conflict)
  → 入口校验（扩展名/魔数/大小/归属）→ 落盘 var/imports/{uuid}.xlsx
  → agent_tasks(import.{entity}, sync, idem=文件hash) 入队 → 返回任务 id
worker(sync):
  _run: openpyxl 流式读 → 表头映射 → 逐行校验 → 分块 upsert（每块提交）
  _finalize: output_json={inserted,updated,skipped,errors[],error_count} + 删文件
GET /agent/tasks/{id} → 导入结果统计 + 行级错误
```

---

## 3. 为什么要这样 build（结构/取舍）

**为什么 reviews 只 insert 不 upsert？** 评论是**事件流**，没有稳定的业务唯一键（同买家同商品可多条；平台评论 id 本系统未存）。强行 upsert 要么造伪键、要么去重口径含糊。故 reviews **append-only**；`conflict=upsert` 对 reviews 直接 400 拒绝（语义不成立），未来要去重再引入 `external_review_id + uniq` 迁移。这与用户「冲突 upsert」选择不冲突——**upsert 是针对有唯一键的实体**，reviews 没有冲突可言（每行都是新事件）。**README/模板会明确写出**避免误解。

**为什么 Excel 用「分块提交」而非 Phase 8 的请求级原子？** 场景不同：Phase 8 是小 JSON、同步、要求「全有或全无」；Excel 是大文件、异步、看重**进度可见 + 中断可续 + 重试安全**，不该把十万行塞进单事务（长锁、大 undo log）。reviews 在分块提交上再加断点续跑游标（见下）；其它实体重跑即幂等。

**为什么只有 reviews 需要「断点续跑」？** **reviews 是 append-only、无唯一键**：若分块提交 + 失败重跑整文件，已提交的评论会被**再插一遍 → 重复**。它没有 upsert 可依，唯一正确做法是**记录已提交行偏移、重试从偏移续跑**（已处理行不再处理），进度与数据同事务提交（崩溃只丢未提交块）。**products/orders/shops 不需要游标**：它们重跑即幂等（upsert / 按订单先删后插），从头跑结果一致——所以按实体区分（§1.5），只给真正需要的 reviews 引入跨重试状态，其它从头跑更简单、且让 orders 的全局 order_no 相邻性校验始终完整。行级错误进报告而非整体回滚（导入工具惯例：尽量入库 + 明确报告坏行）。

**为什么 orders 必须按 order_no 边界切块 + 校验相邻？** orders 用「订单头 upsert + 明细先删后插」重建单据；若同一 `order_no` 的明细被切到两块，第二块的 `DELETE items` 会清掉第一块刚插的明细。故**切块只在 order_no 边界**——保证「一个订单的所有明细在同一事务内删+插」。更进一步：若同一 `order_no` 在文件里**非相邻**地出现两次（中间夹别的单），后一组的「先删后插」也会覆盖前一组（数据丢失）。故 worker 单次遍历维护「已关闭 order_no」集合，**重现即整文件失败**（结构错）；因 orders 重试是从头整文件重跑（§1.5），该集合在单次遍历内始终完整、检测无盲区。模板要求同一订单明细行相邻。

**为什么逐实体拆 service？** 四类实体列结构、唯一键、外键解析、upsert 字段各不同；拆成 `imports/{shops,products,orders,reviews}.py` 各自「parser + upsert」，可独立单测（喂行 dict → 断言 upsert SQL/统计），主任务只做编排（沿用 `_run/_finalize`）。

**复用既有不变量**：多租户 account→shop 双侧校验、`agent_tasks` 幂等/状态机/reaper、Phase 7 `on_duplicate_key_update` 与「先删后插」、Phase 8 分块大小配置。**不新增 LLM**（纯确定性 ETL）。

---

## 4. 验收标准（Phase 9）

- 上传合法 products.xlsx（含已存在 sku 与新 sku）→ 任务完成，`output_json` 的 inserted/updated 与手算一致；已存在 sku 的 price/cost 被更新（upsert），无重复行。
- orders.xlsx（一单多行明细）→ 订单头 upsert、明细先删后插；重跑同文件无重复明细、利润字段不被破坏（profit 由 Phase 6 另算，导入不写 profit）。
- reviews.xlsx → 仅 insert；`conflict=upsert` 对 reviews 返回 400。
- shops.xlsx（external_shop_id 必填）→ 按 (account_id, external_shop_id) upsert；缺该列报结构错；**shops 导入任务（shop_id=NULL）能被本租户 `GET /agent/tasks/{id}` 查到进度**（验证 account_id 授权，Codex #2）；他租户查不到。
- 非 .xlsx / 伪造扩展名（魔数不符）/ 超大文件 → 入口 400/413，不落盘不建任务。
- 坏行（类型错/缺必填/跨 shop 的 sku）→ 计入 errors（带行号，≤200 条上限 + 总数），好行照常入库；结构性错误（缺必填列、**orders 非相邻 order_no**）整文件拒绝。
- 越权：shop 不属当前 account → 400/404，不入队；worker 侧按 account_id 复核。
- 幂等：同文件重传命中幂等键不重复处理；`force` 重跑；upsert 使重跑结果一致。
- **reviews 断点续跑（重点）**：处理到第 K 块后崩溃 → retry 从 `processed_rows` 续，最终 reviews 总数 = 文件行数（**无重复**）；products/orders/shops 崩溃后从头重跑结果一致、无重复。
- **错误报告落地（Codex #3）**：任务 **failed** 后 `GET /agent/tasks/{id}` 仍能拿到 `output_json` 的 errors/error_count/processed_rows。
- **进度可见**：处理中 `output_json.progress.processed_rows` 单调推进。
- **orders 相邻校验（Codex #1）**：构造非相邻同 order_no 文件 → 整文件失败、错误指明违例 order_no、零脏写。
- 任务成功终态后上传文件被删除；失败/重试中文件保留；失败任务保留 `error_*` + output_json。
- 单元测试：表头映射（大小写/空格/缺列）、各实体行校验、products/shops upsert 统计、orders 先删后插 + 非相邻 order_no 整文件失败、reviews 拒 upsert + 断点续跑不重复、进度与数据同事务、account_id 授权（shops 任务可查/越权不可查）、失败仍可查 output_json、魔数/大小入口校验、行错误上限不静默。

## 5a. 实施时必须钉死（供 Codex 重点审阅）

1. **文件安全**：扩展名 + ZIP 魔数双校验；大小上限；生成名落盘（防穿越）；`read_only=True, data_only=True`（流式 + 不执行公式）；上传目录不可被 web 直接访问；成功后删文件、失败/重试中保留。
2. **reviews 不 upsert**：`conflict=upsert`（或默认被解释为 upsert）对 reviews 一律 400；reviews 走 insert-only；理由与未来 external id 去重在 README/模板写清。
3. **shops 唯一键迁移（Codex #5）**：迁移 0005 加 `uniq(account_id, external_shop_id)` + 同步模型；`upgrade()` **先 SELECT 检测非 NULL 重复键**，有则 `raise` 列出冲突、要求先清理（不静默建失败）；sheet `external_shop_id` 必填非空、空值整行报错。
4. **事务/进度/重试（核心）**：每块「数据 + 进度 `output_json`」**同一事务提交**（用 `update_import_progress`，状态门禁 running、仅改 output_json）。重试按实体（§1.5）：**reviews 断点续跑**（从 `processed_rows`，append-only 防重复）；**products/orders/shops 从头整文件重跑**（upsert/先删后插幂等）。任务幂等键 = 文件内容 sha256。明确**非请求级原子**，README 说明。
5. **错误报告落地（Codex #3）**：`output_json` 每块就提交，故 succeeded/failed/retry 任一终态都能查到 `{errors,error_count,processed_rows,inserted/updated/skipped}`；`mark_failed/mark_retry` 只改 status/error_*、**不得覆盖 output_json**；进度写入失败不可吞成成功。
6. **orders 边界 + 相邻校验（Codex #1）**：按 order_no 边界切块（同订单明细同事务先删后插，不切在订单中间）；单次遍历维护 seen-order_no 集合，**非相邻重现 → 整文件失败**；orders 从头重跑使检测无盲区；模板要求同订单行相邻。
7. **任务授权 account_id（Codex #2）**：迁移 0006 给 `agent_tasks` 加 `account_id`（+回填存量 from shop）；`create_task` 增 account_id 入参、**所有既有建任务处补传**；`get_for_account/cancel_for_account` 改按 `account_id` 授权——使 shops 导入（shop_id=NULL）任务可被本租户查询/取消；不破坏既有状态机/幂等/reaper。
8. **Celery 路由精确（Codex #4）**：路由 `app.tasks.imports.* → sync`（真实任务名前缀，非 `import.*`）；celery `include` 加该模块；worker `-Q ...,sync`；`import.{entity}` task_type 进 reaper registry；入队失败留可见失败记录。
9. **租户双侧校验**：API 校验 shop 属 account（shops 导入校验 account 自身）；worker 按 `agent_tasks.account_id` + `input_json` 复核；所有 upsert/外键解析按 shop 限定，禁跨租户。
10. **行级错误不静默**：errors 带行号、上限 `import_max_row_errors`，超限只计数但**报告总错误数**；区分「结构性错误（缺必填列/orders 非相邻）→ 整文件拒绝」与「行错误 → 跳过该行、继续」。
11. **金额/类型**：金额 `Decimal`/`DECIMAL(12,2)`，禁 float；数量/库存 int；rating 1–5；解析失败计行错误。
12. **不引入重栈 + 大文件内存**：仅 openpyxl（已说明）、不引 pandas、不新增 LLM；openpyxl `read_only` 流式逐行、不一次性载入；分块入库 + 上传大小上限兜底。

## 5. 不在本阶段做
- CSV/JSON 文件导入、导出、列映射 UI、模板下载接口（先给 md 模板）。
- reviews 去重（external_review_id）、orders 增量/部分更新语义。
- 跨机共享上传存储（VM/对象存储）、断点续传、并发同文件锁（幂等键已兜底）。
- **终态失败/孤儿上传文件的定期清理（janitor）**：成功即删；失败/重试保留文件（reviews 续跑、其它重跑需要）；达终态 failed 的残留文件留待后续清理任务回收（本阶段不实现）。
- 前端上传页（前端阶段）。
