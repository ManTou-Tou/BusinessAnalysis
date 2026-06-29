# Phase 8 方案：高并发优化（API 限流 / 索引体检 / 批量写入 / 分布式锁）

> **状态：草案 v5（第 1–3 轮设计问题已整改；v5 整改第 4 轮 4 项纯文本一致性：①「滑动窗口」统一为「固定窗口」②③ 跨店低频路径措辞统一为「既不带 shop_id 也不带 product_id」④ EXPLAIN 验证路径列表与 §4 对齐。Codex 已确认索引设计本身通过，索引共 9 个）—— 待 Codex 第 5 轮复审确认收口。通过前不得写代码、不得采用。**
> 规则见 CLAUDE.md / CODEX_REVIEW_POLICY.md。设计总纲见 DESIGN.md（§3 高并发要求、§10 Phase 8「按需」、行 112-114 分页、行 124 限流）。
> 需求来源 project.md「## High Concurrency Requirements」「## Redis Usage」「## Database Requirements」。
> 底座见 PHASE_3_PLAN.md（任务幂等/队列/Beat）、PHASE_5_PLAN.md（worker 侧 LLM 限流）、PHASE_7_PLAN.md（Beat 派发）。

---

## 0. 本阶段定位与边界（先说不做什么）

project.md 把 Phase 8 写为「高并发优化、索引优化、批量处理、限流」，DESIGN §10 又把「平台 API / ES / read replica / 其余 Agent / 前端 / Docker 部署」也笼统挂在 Phase 8（标注「按需」）。**本轮只做高并发优化这一块**，与 MVP「做减法」一致；其余方向各自留作独立 Phase（见 §6）。

对照 project.md「High Concurrency Requirements」逐项盘点现状：

| 要求 | 现状 | 本阶段动作 |
| --- | --- | --- |
| 分页查询 | ✅ keyset 已有（`utils/pagination.py`） | 补**支撑索引**（见下），分页代码不动 |
| 索引优化 | ⚠️ 基础索引在，但**未覆盖实际热查询的排序键** | 体检 + 迁移 `0004` 补覆盖索引 |
| 批量插入 | ⚠️ `import_reviews` 用 ORM `add_all`、无分块/上限 | Core 批量插入 + 分块 + 单次上限 |
| 批量更新 | ✅ Phase 6 利润回写已是批量 | 不动（已达标） |
| 异步任务 / 重试 / 去重 | ✅ Phase 3 已落（Celery + `idempotency_key`） | 不动 |
| 分布式锁 | ❌ 无；Beat 仅「单实例假设」 | Redis `SET NX PX` 锁工具 + 套到 Beat 派发器 |
| API 限流 | ❌ HTTP 入口无限流（只有 worker 侧 Celery `rate_limit`） | Redis 固定窗口限流依赖 + 套到写/重接口 |

> **缓存（Redis Usage 列了 Cache）**：列为**可选子项**（§3 第 5 条），默认建议**本轮做最小只读缓存**或整体后置，请用户/Codex 在过审时拍板，避免一次性铺太大。

---

## 1. 本阶段做什么（范围 + 改动文件）

### 1.1 API 限流（Redis 固定窗口，核心新增）

- 新增 `app/core/ratelimit.py`：基于 Redis 的**固定窗口计数**（`INCR` + 首次 `EXPIRE`，单条 Lua 脚本保证原子，避免 INCR 成功但 EXPIRE 丢失导致 key 永不过期）。返回是否放行 + 剩余配额 + 重置秒数。
- 新增 FastAPI 依赖 `RateLimit(scope, limit, window_s)`（放 `app/api/deps.py` 或 `ratelimit.py`）：限流维度 = `account_id + 路由标识`（拿不到 account 时退化到客户端 IP）。命中上限返回 **429** + `Retry-After` 头。
- 套用范围（先窄后宽，只挂**写操作与昂贵触发接口**，只读列表先不挂或给宽松额度）：
  - `POST /reviews/import`、`POST /reviews/classify`、`POST /agent/*`（profit-analysis / daily-report / review-classifier）、`POST /shops|/products|/orders`。
- **失败开放（fail-open）**：Redis 不可用时记日志并放行，不让限流组件成为单点把整个 API 打挂（限流是保护，不是依赖）。
- 配置（`config.py`，env 可调，给默认值）：`ratelimit_enabled=True`、`ratelimit_default_limit`、`ratelimit_default_window_s`，以及少量按接口覆盖（如 `ratelimit_llm_trigger_limit` 给 LLM 触发类更紧的额度）。

### 1.2 索引体检 + 迁移 `0004`（对齐实际查询路径）

逐条把「代码里真实跑的查询」与「现有索引」对账，补**缺失的覆盖/排序索引**（不删旧索引，避免影响既有路径）。**已穷举 backend/ 全部高频查询**（list 服务 / 批量分类 / 日报聚合 / reaper / analytics），分「需新增」「已覆盖（不动）」两类列清，避免遗漏热路径（Codex 第 1 轮 #1/#2）：

**A. 需新增（迁移 0004，仅 `op.create_index`）：**

| # | 热查询（来源） | 现状索引 | 问题 | 0004 新增 |
| --- | --- | --- | --- | --- |
| a | `reviews` keyset（**带 shop_id，不带 product_id**）：`WHERE shop_id=? ORDER BY id DESC`（`review.list_reviews`:82） | `(product_id,created_at)`、`(shop_id,review_type)` | shop 维度排序键非 `id` → filesort | `(shop_id, id)` |
| a2 | `reviews` keyset（**带 product_id**，无论是否带 shop_id）：`WHERE product_id=? [AND shop_id=?] ORDER BY id DESC`（`review.list_reviews`:90；API 允许 `product_id` 单独过滤，`reviews.py`:40） | `(product_id,created_at)` 不支撑 `id` 序 | 单商品大量评论下 → filesort | `(product_id, id)`（product_id 高选择度，**同时覆盖** product_id-only 与 shop_id+product_id；后者 shop_id 仅作残余过滤、近乎免费） |
| b | 批量分类选取：`WHERE shop_id=? AND review_type IS NULL ORDER BY id LIMIT n`（`review.enqueue_classification`） | `(shop_id,review_type)` | 排序键 `id` 不在索引尾 | `(shop_id, review_type, id)`（NULL 过滤 + 有序 limit，**最高价值**） |
| c | `products` keyset（带 shop_id）：`WHERE shop_id=? ORDER BY id DESC`（`product.list_products`） | `(shop_id,status)` | 排序键非 `id` → filesort | `(shop_id, id)` |
| h | `orders` keyset（带 shop_id）：`WHERE shop_id=? ORDER BY id DESC`（`order.list_orders`:78） | `(shop_id,created_at)` | **排序键 `id` ≠ created_at**，MySQL 不会用 `(shop_id,created_at)` 服务 `ORDER BY id` → filesort | `(shop_id, id)` |
| d | **日报评论聚合**：`WHERE shop_id=? AND created_at∈[d,d+1)`（总数/负面计数）（`report.aggregate`:159） | 无（`(shop_id,review_type)` 不含 created_at） | 全索引扫 + 范围回表 | `(shop_id, created_at)` |
| e | **日报负面分组**：`WHERE shop_id=? AND sentiment='negative' AND created_at∈[d,d+1)` GROUP BY review_type（无 ORDER BY id）（`report.aggregate`:167） | `(sentiment)` 单列、低选择度 | 命不中复合条件 | `(shop_id, sentiment, created_at)` |
| f | **reaper 扫 pending 孤儿**：`WHERE status='pending' AND celery_task_id IS NULL AND created_at<阈值`（`agent_task.reap`:258） | `(status,task_type)` 不含时间 | 1M tasks/day 下全 status 扫描 | `(status, created_at)` |
| g | **reaper 扫 running 卡死**：`WHERE status='running' AND started_at<阈值`（`agent_task.reap`:266） | 同上 | 同上 | `(status, started_at)` |

> **关于 `ORDER BY id` 后的 filesort（取舍说明，Codex 第 2 轮 #1/#2）：**
> - (a/a2/c/h) keyset 列表：新增的 `(...,id)` 把 `id` 放索引尾，过滤是**等值**（shop_id/product_id），等值后 id 即索引序，**可消除 filesort**——这是必须达标项。
> - (b) 批量分类：`review_type IS NULL` 是等值（NULL），之后 id 仍索引序，**可消除 filesort**。
> - (e) **负面抽样**（`report.aggregate`:173 `ORDER BY id LIMIT report_negative_sample`）：因前置 `created_at∈[范围]` 是**范围**条件，其后 id **无法**再走索引序——**接受小 filesort**：当天某店负面评论集很小、`limit` 默认 20，排序代价可忽略。**不为它单建索引**（`(shop_id,sentiment,id)` 会丢 created_at range 过滤、得不偿失）。该抽样查询**不纳入** §4「无 filesort」断言。
> - (d/e 的计数/分组) 无 `ORDER BY id`，`(shop_id,created_at)`/`(shop_id,sentiment,created_at)` 直接服务范围 + 分组，**应无 filesort**。

**B. 已覆盖、本阶段不动（盘点留痕，防"漏掉"误判）：**

| 查询 | 既有索引 | 结论 |
| --- | --- | --- |
| 日报订单聚合 `WHERE Order.shop_id=? AND created_at∈[...]`（`report.aggregate`:108/116/129） | `ix_orders_shop_created(shop_id,created_at)` | ✅ 已最优 |
| 日报商品聚合 join `OrderItem.order_id=Order.id` / `product_id` | `ix_order_items_order`、`ix_order_items_product` | ✅ 已覆盖 |
| analytics 商品利润 `WHERE OrderItem.product_id=? AND profit IS NOT NULL`（`analytics`:77） | `ix_order_items_product(product_id)` | ✅ 覆盖（profit 过滤回表，量小可接受；见 §3.5 缓存边界） |
| `*_daily_metrics`/`daily_reports` 读 `uniq(shop_id,date)` / `uniq(product_id,date)` | Phase 7 唯一键 | ✅ 单行命中 |
| agent_tasks 状态查询 `(status,task_type)`、`(celery_task_id)`、`(shop_id,created_at)` | Phase 3 索引 | ✅ 覆盖 |

**C. 「既不带 shop_id 也不带 product_id」的整-account 跨店列表 —— 显式决策（Codex 第 1 轮 #3）：**
`list_products/orders/reviews` 当**两个过滤都不传**时按 `JOIN shops WHERE shops.account_id=? ORDER BY 实体.id DESC` 翻页，新增的等值前缀索引**都用不上**（排序键是实体 PK、过滤在 join 后的 shops.account_id）。本阶段**决策**：
- **快路径**：带 `shop_id`（走 `(shop_id,id)`）**或**带 `product_id`（走 `(product_id,id)`，见 a2）均已被 0004 覆盖、无 filesort。**唯有「两者都不带」的整-account 跨店列表**定位为**低频管理/概览路径**，靠 PK 倒序扫 + nested-loop 过滤 account，**keyset `limit≤100` 限定单页代价**。
- **不**为此denormalize `account_id` 到各业务表（改动大、写放大，属"未来"而非本优化阶段）；风险边界写进 README：account 下店铺数极多 + 深翻页时该路径会退化，届时再评估冗余 `account_id` 列 + `(account_id,id)` 索引。
- 前端/调用方约定：列表默认带 `shop_id`（已是常用法）。本阶段**不改接口签名**，仅文档化取舍。

- 迁移**仅 `op.create_index`**，不改表结构、不删列；每个新索引在迁移注释里写明「服务哪条查询（带行号）」；`downgrade` 正确 drop。
- 验证：方案/实施附 `EXPLAIN` 对照（造数后跑 **a/a2/c/h（keyset）、b（批量分类）、d/e 分组（日报计数）、f/g（reaper）** 关键路径），确认走新索引、`Extra` 不再 `Using filesort`/全扫；唯 e 的负面**抽样**按上文取舍允许小 filesort。

### 1.3 批量写入优化（reviews 导入）

- `services/review.import_reviews`：把 `db.add_all(ORM 对象)` 改为 **SQLAlchemy Core 批量插入**（`insert(Review), [dict,...]`，一次多行 VALUES）。
- **事务边界 = 请求级原子（全有或全无）**（Codex 第 1 轮 #4）：所有行在**同一事务**内分块 **flush**（每 `bulk_insert_chunk_size`，默认 1000，控制单条 SQL 包大小 / 内存），**末尾一次 `commit`**；任一块失败 → 整体 `rollback`、返回 5xx/400、不留半截导入。**不逐块提交**，从而保持现有「成功即返回插入条数」的全成功语义，无 partial-success 歧义。
- 新增**单次导入上限** `review_import_max_per_request`（默认 5000，与 `llm_classify_max_per_request` 风格一致）：超出返回 400，提示分批（不静默截断）。上限同时是单事务大小的护栏（5000 行 × flush 分块，事务 undo log 可控）。
- 校验逻辑（product/order 归属）保持不变，仍在插入前一次性批量校验（现状已是 `IN (...)` 批量，不退化）。
- 返回值语义不变（插入条数）。README 注明：**单次导入是原子的**；要导更多请调用方分批，每批各自原子。

### 1.4 分布式锁（Redis）+ Beat 派发去重

- 新增 `app/core/lock.py`：`redis_lock(key, ttl_s)` 上下文管理器，`SET key token NX PX ttl`；释放用 **Lua「比对 token 再删」**（防误删别人续上的锁）。获取失败 → 不执行、记日志返回。
- 套到 `tasks/report.dispatch_daily_reports`：进入即抢 `lock:dispatch_daily_reports:{date}`，抢不到直接返回（说明另一实例在派发）。**幂等键 + upsert + `source_task_id` 守卫仍是兜底**——锁只是减少重复派发的惊群，不是正确性唯一来源（双保险）。
- 这样 Beat 从「单实例假设」升级为「多实例安全」，兑现 project.md「分布式锁」要求；DESIGN 行 125 原把多实例锁后置，本阶段实现它并更新设计。

### 1.5 连接池参数走配置（小项）

- `core/config.py` 暴露 `db_pool_size`(默认10) / `db_max_overflow`(默认20) / `db_pool_recycle`(默认1800)，`core/db.py` 改读 settings。便于压测期按机器调，不改默认行为。

### 涉及文件（预估）

- **新增**：`app/core/ratelimit.py`、`app/core/lock.py`、`alembic/versions/0004_high_concurrency_indexes.py`、`tests/test_ratelimit.py`、`tests/test_review_bulk_import.py`、`tests/test_redis_lock.py`
- **改动**：`app/api/deps.py`（RateLimit 依赖）、`app/api/v1/{reviews,agents,shops,products,orders}.py`（挂限流依赖）、`app/services/review.py`（Core 批量插入 + 上限）、`app/tasks/report.py`（dispatch 加锁）、`app/core/db.py`（池参数读配置）、`app/core/config.py`（限流/批量/池配置）、`backend/README.md`、`.env.example`、`DESIGN.md`（§4 锁、§3 索引、§10 进度）

---

## 2. 为什么用这种逻辑跑（运行 / 数据流 / 编排的理由）

**为什么 API 限流用 Redis 固定窗口 + Lua，而不是内存计数或令牌桶库？**
内存计数在多 worker/多进程下各算各的，挡不住总量——必须跨进程共享状态，Redis 是现成且已在用的共享层。固定窗口（`INCR`+`EXPIRE`）实现最简、O(1)、够用于「保护后端不被打爆」；滑动日志/令牌桶更精确但更重，MVP 不需要。用 **Lua 把 INCR 与 EXPIRE 合成一次原子调用**，避免「INCR 成功、EXPIRE 因崩溃丢失 → key 永不过期、配额永久锁死」的经典 bug。**fail-open** 是刻意取舍：限流是「保护层」，它自己挂了不该连带把正常请求也拒掉。

**为什么限流维度是 account+路由，而不是全局或纯 IP？**
本系统是多租户，资源消耗按 account 隔离才公平（一个租户狂刷不该饿死别人）；按路由分是因为 `POST /agent/daily-report`（触发 LLM、贵）和 `GET /shops`（便宜）该有不同额度。拿不到 account（未带头/无效）时退化到 IP，保证匿名/异常流量也有兜底。

**为什么只给写/触发接口挂限流，只读列表先放宽？**
高并发风险主要在**写入与昂贵任务触发**（批量导入、LLM 触发会放大成本与 DB 压力）。只读 keyset 列表本身已轻且有索引，先给宽松额度或不挂，避免误伤正常浏览；范围「先窄后宽」便于观察再调。

**为什么批量插入要分块 + 设单次上限？**
万级 `add_all` 会：①累积大量 ORM 状态、②生成超大单条 SQL/单个长事务，锁表久、回滚代价高、易撞 `max_allowed_packet`。分块（1000/批）把事务切小、内存稳定、失败影响面小。单次上限（5000）是入口护栏，超量要求调用方分批，与 Phase 5「不静默截断、显式 remaining/上限」的既定风格一致。

**为什么分布式锁要兜底而非唯一保证？**
锁会因 TTL 到期、Redis 故障、进程暂停而失效——把正确性**只**寄托在锁上是危险的。本系统派发本就幂等（`idempotency_key` + upsert + `source_task_id` 守卫，Phase 7 已验证），所以锁的职责降为「日常减少重复派发的惊群」，即使锁偶尔失效，幂等层仍保证不重复入队/不脏写。这是「锁优化吞吐、幂等保正确」的分层。

**数据流（限流）：**
```
HTTP 请求 → 路由依赖链：get_account_id → RateLimit(scope, limit, window)
  RateLimit: key = rl:{scope}:{account_or_ip}:{window_bucket}
             Lua: n=INCR(key); if n==1 then PEXPIRE(key, window) end; return n
             n>limit → 抛 429 + Retry-After=重置秒数
             Redis 异常 → 记日志、放行（fail-open）
  通过 → 进入原 handler（业务逻辑不变）
```

**数据流（Beat 派发加锁）：**
```
Beat(每日 02:00 UTC) → dispatch_daily_reports(date=昨天)
  with redis_lock("lock:dispatch_daily_reports:{date}", ttl=派发预算):
      遍历 shops → 逐店 create_task(幂等键) + 入队（原逻辑不变）
  抢锁失败 → 直接 return（另一实例在派发）；幂等键仍兜底
```

---

## 3. 为什么要这样 build 代码（结构 / 取舍 / 权衡的理由）

1. **限流抽成 `core/ratelimit.py` + FastAPI 依赖**：与 `core/redis.py`、`api/deps.py` 现有分层一致；做成依赖（而非中间件）能**按路由精细配额**、能复用 `get_account_id` 的解析结果、能被单测直接调用。中间件难按路由差异化、且会拦健康检查等不该限流的端点。

2. **索引迁移只增不改不删**：新增覆盖索引零破坏既有查询；不动旧索引避免「优化一条、拖慢另一条」。每个索引绑定一条具体查询并附 `EXPLAIN`，杜绝「凭感觉加索引」——加错索引会拖慢写入、占空间。

3. **批量插入用 Core 而非 `bulk_save_objects`**：Core `insert().values(list)` 语义清晰、不触发 ORM 事件/身份映射开销，且与 Phase 6/7 已用的 `insert(Model), [dict]` 写法统一。分块大小、单次上限走 `config`，压测可调不改码。

4. **锁与限流都 fail-open / 兜底**：二者都是「优化/保护」而非「业务正确性来源」。Redis 抖动不该让导入失败或日报漏跑。正确性始终落在 MySQL 事务 + 唯一约束 + 幂等键上（Phase 3/6/7 已建立的不变量），Redis 只加速/降压。

5. **缓存——本轮后置，风险边界已界定（Codex 第 1 轮 #5）**：先把瓶颈用限流 + 索引压住，再按压测决定是否加缓存，避免过早优化 + 一致性负担。逐个只读热路径界定后置风险：
   - `GET /analytics/shop/{id}/daily|report`：**单行命中** `uniq(shop_id,date)`（Phase 7），无实时聚合，本就 O(1)，不需要缓存。
   - `GET /analytics/product/{id}/profit`（`analytics.py`:77）：**唯一的实时聚合只读路径**（对 `order_items` 按 `product_id` SUM/COUNT）。后置缓存后，本阶段靠两道闸控住：① **限流**给该路由配额（防被刷爆 DB）；② 索引 `ix_order_items_product(product_id)` 命中、`profit IS NOT NULL` 仅回表过滤，单商品明细量有界。**风险边界**：单个超热商品被高频重复查询时会重复聚合（无 memo）；一旦压测显示该路径是瓶颈，**下一步**就是给它加 `(product,calc_version)` 短 TTL 读穿透 + Phase 6 `_finalize` 成功后失效——方案与失效点已预留（见 §5a#12），届时单独小步上。
   - 结论：**本轮不实现任何缓存**；§5a#12 仅作"若将来纳入"的一致性约束登记，不在 Phase 8 落地。

6. **与现有结构一致**：沿用 `core/*` 基础设施分层、`api/deps.py` 依赖注入、`config.py` 集中配置、Core 批量写法、`finalize_succeeded` 同事务、幂等键不变量；不引入新依赖（限流/锁均用现有 `redis` 客户端，不加 `slowapi`/`redlock` 等库，符合「栈外技术需先说明」原则）。

---

## 4. 验收标准（Phase 8）

- **API 限流**：对某 account 在 window 内连发超过 limit 次 `POST /reviews/import` → 超额请求返回 **429 + Retry-After**；换 account 不受影响（维度隔离）；窗口过后恢复。**Redis 停掉** → 请求仍放行（fail-open），日志有告警。`ratelimit_enabled=False` 时完全旁路。
- **索引**：对 §1.2.A 路径造数后 `EXPLAIN`——keyset(a/a2/c/h)、批量分类(b)、日报计数/分组(d/e 分组)、reaper(f/g) 确认命中新索引且 `Extra` **无 `Using filesort`**/无全表扫；**例外**：日报负面**抽样**（e 的 `ORDER BY id LIMIT`，`report.aggregate`:173）因 created_at 范围在前，**允许小 filesort**（limit≤20，不纳入无-filesort 断言，见 §1.2.A 取舍说明）。§1.2.B 已覆盖项回归不变；§1.2.C「既不带 shop_id 也不带 product_id」的整-account 跨店列表行为不变（文档化取舍，不强制索引；带 product_id 单独过滤已由 a2 `(product_id,id)` 覆盖、非低频路径）。
- **批量导入**：导入 3000 条评论成功且条数正确（**单事务原子**）；超过 `review_import_max_per_request` 返回 400（提示分批，不静默截断）；**中途模拟失败 → 整体回滚、零写入**（验证原子性）；归属校验（跨店 product/order）仍正确拦 400。
- **分布式锁**：并发跑两次 `dispatch_daily_reports(same date)` → 仅一个进入派发、另一个抢锁失败返回；即便强制两个都进（模拟锁失效），幂等键仍保证不重复入队/不重复落库（兜底验证）。锁正常释放（TTL 内完成则主动释放，异常则 TTL 兜底）。
- **连接池**：`db_pool_size` 等经 env 生效（改值后连接数变化可观测），默认值与现状一致、行为不变。
- **单元测试**：限流计数/窗口/fail-open/429、Lua 原子性（EXPIRE 不丢）、批量插入分块与上限、归属校验回归、Redis 锁的 NX 互斥与「比对 token 再删」、连接池配置读取。
- **不破坏既有**：Phase 1–7 全部测试通过；`/health`、`/ready` 不变；worker/Beat 启动命令不变。

## 5a. 实施时必须钉死（供 Codex 重点审阅）

1. **限流 Lua 原子 + key 必然过期**：`INCR` 与 `EXPIRE` 必须在**同一 Lua**里（首次计数才设过期），杜绝「计数留存、过期丢失 → 配额永久锁死」。window key 用时间桶（`floor(now/window)`）或纯 INCR+PEXPIRE，二选一并在方案注释说明边界行为（固定窗口边界突发是已知取舍，MVP 接受）。
2. **fail-open 必须真的开放**：限流组件内**所有** Redis 异常都 `try/except` → 记日志 + 放行；绝不把异常冒泡成 5xx。单测覆盖「Redis 抛错仍放行」。
3. **限流不挂到健康检查/内部接口**：`/health`、`/ready`、dev 桩（`require_dev_env` 保护的）不挂限流，避免探针被限。
4. **429 语义完整**：返回 `429 Too Many Requests` + `Retry-After`（秒）头；body 给出 scope/limit/重置时间，便于客户端退避。
5. **维度键防碰撞 + 防膨胀**：限流 key 命名空间 `rl:{route_id}:{account_or_ip}:{bucket}`，route_id 用稳定常量（非原始 path，避免路径参数撑爆 key 空间）；key 一律带 TTL（随窗口过期），不留垃圾。
6. **批量插入 = 请求级原子（已定，Codex 第 1 轮 #4）**：`chunk_size`、`max_per_request` 从 config 读且 `ge=1`/有合理上限；空列表早返回 0。**单事务 + 分块 flush + 末尾一次 commit**；任一块异常整体 rollback、不部分写入；返回插入条数（= 全部或 0）。README 注明单次原子、超量请分批。**不采用逐块提交**（避免 partial-success 与现有返回模型不符）。
7. **锁 token 唯一 + 安全释放**：`SET NX PX` 的 value 用每次调用唯一 token；释放用 Lua `if get==token then del`，禁止裸 `DEL`（防误删续锁）。TTL 取「派发最坏耗时」的安全上界，避免锁提前过期导致双进。
8. **锁失效不破坏正确性**：必须保留并测试「锁失效 → 幂等键兜底」路径；锁只减惊群，不作唯一正确性来源（dispatch 已幂等）。
9. **索引只增不删、绑定查询、附 EXPLAIN、穷举热路径**：迁移 0004 新增 §1.2.A 的 **9 个索引**——keyset：`reviews(shop_id,id)`、`reviews(product_id,id)`、`products(shop_id,id)`、`orders(shop_id,id)`；批量分类 `reviews(shop_id,review_type,id)`；日报 `reviews(shop_id,created_at)`、`reviews(shop_id,sentiment,created_at)`；reaper `agent_tasks(status,created_at)`、`agent_tasks(status,started_at)`。每个索引注释写明服务的查询 + 源码行号；`downgrade` 正确 drop；不删/改名旧索引；已覆盖项（§1.2.B）不重复加；「既不带 shop_id 也不带 product_id」的跨店列表按 §1.2.C 文档化、不加索引（`reviews(shop_id,id)` 必须保留服务 shop-only 路径，不能被 `reviews(product_id,id)` 替代）。**`orders(shop_id,id)` 必加**——`ORDER BY id` 不能复用 `(shop_id,created_at)`（MySQL 不因 id/created_at 相关性等价替换，Codex 第 2 轮 #1）；该索引兑现 §1.2.C「orders 带 shop_id 为快路径」承诺。
10. **不引入栈外依赖**：限流/锁均用现有 `redis-py` 实现，不加 `slowapi`、`redis-py-lock`、`redlock` 等；如确需，先在方案说明理由并经 Codex（符合 CLAUDE.md 栈约束）。
11. **配置默认 = 现状行为**：所有新开关默认值使系统行为与 Phase 7 末态一致（限流默认开但额度宽松、池参数默认同现值），确保「合并即不变」，调优是显式动作。
12. **缓存（若纳入）一致性钉死**：读穿透 key 必含 `calc_version`；写侧 `_finalize` 成功后**同步失效**对应 key；失效失败不可吞成「成功」。若本轮不做缓存，方案明确标注后置（默认建议后置）。

## 5. 明确不在本阶段做（留待后续 Phase）

- 真实 Shopee/TikTok/Lazada 平台 API 对接与 `sync` 队列实任务。
- Elasticsearch 搜索、read replica、partition table、sharding（DESIGN「未来」；仅保持模型不堵死）。
- 其余 LLM Agent：Product Analysis / Listing Writer / Reply Generator。
- 前端 React、Docker/VM(Xterminal) 部署、Jenkins CI。
- 正式鉴权（登录/Token）替换 `X-Account-Id`（限流维度届时随之切换到真实身份）。
- 令牌桶/滑动日志等更精确限流算法、per-endpoint 动态配额下发（固定窗口够用即止）。
- （倾向后置）热点只读缓存——见 §3 第 5 条，过审定夺。
