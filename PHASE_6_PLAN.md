# Phase 6 方案：Profit 计算（确定性，非 LLM）+ 接入 CRUD/报告

> **状态：方案 Codex APPROVED（2026-06-20，经两轮整改）；代码已实现并经 Codex APPROVED（2026-06-20，经一轮整改）。Phase 6 完成。**
> 规则见 CLAUDE.md / CODEX_REVIEW_POLICY.md。设计总纲见 DESIGN.md（§3.18、§5、行 122/138）。
> 底座见 PHASE_3_PLAN.md（异步任务生命周期）、PHASE_4/5_PLAN.md（任务 `_run`/`_finalize` 模式、`finalize_succeeded` 同事务回写）。

---

## 1. 本阶段做什么

实现**利润计算**：把订单明细 + 商品成本 + 订单级费用，按确定性公式算出每条 `order_items` 的利润并回写，并提供按商品聚合的利润分析读接口。**全程不调 LLM**（DESIGN §3.18：确定性计算用 Python/SQL，LLM 只做分类/文案/总结）。

范围：

1. **利润计算服务** `app/services/profit.py`：纯函数（无 I/O、无 session），输入一张订单（含明细 + 订单级费用），输出每条明细的 `gross_profit / net_profit / profit_margin / break_even_price` 与订单级汇总。用 `Decimal` 全程计算，订单级费用按收入占比分摊到明细，并用**最大余数法**保证分摊额之和精确等于订单费用（无四舍五入漂移）。
2. **compute_profit 任务** `app/tasks/profit.py`（queue=`default`）：按 Phase 3 `run_lifecycle` 跑；`_run` 只读载入某 shop 的订单（worker 侧二次校验租户归属）+ 调 `services/profit.py` 计算；`_finalize` 在**同一事务**内把每条 `order_items.profit` 回写（限定 `shop_id` 兜底）并 `finalize_succeeded`，把本次计算的汇总/计数记入 `agent_tasks.output_json`。
3. **触发 API** `POST /api/v1/agent/profit-analysis`（对应 project.md「POST /agent/profit-analysis」）：入参 `shop_id`（校验归属）+ 可选 `order_id`（只算单订单）、`force`（默认 false；幂等语义与 Phase 4/5 一致，见 §5a#6）；建 `agent_tasks`（`task_type=profit.compute`、`shop_id`、`entity_type=order`/`shop`）并入队 `default`，返回业务任务 id；状态仍走 `GET /agent/tasks/{id}`。
4. **利润读接口** `GET /api/v1/analytics/product/{product_id}/profit`（对应 project.md「Analytics APIs」）：**同步只读聚合**，按当前 account → shop → product 归属校验，连 `order_items ⨝ orders` 聚合该商品的 `units_sold / revenue / cogs / fees / gross_profit / net_profit / profit_margin / break_even_price`，返回结构化结果。
5. **任务注册**：`profit.compute → compute_profit_task` 加进 Phase 3 maintenance 重入队 registry（孤儿回收可重投）。
6. **配置**：把可调旋钮加进 `app/core/config.py` 的 `Settings`（与既有风格一致，用 `Field` 约束）：`profit_max_orders_per_run: int = Field(default=1000, ge=1)`、`profit_calc_version: str = "v1"`；并同步 `.env.example` 与 `backend/README.md`。
   > **不**引入 `profit_fee_allocation` 全局口径开关：正常分摊**固定**按收入（§5a#2），仅 `Σrevenue==0` 时在**代码内固定**退化为按 quantity——不暴露为配置，避免绕开已钉死的分摊分母口径产生分叉。

涉及文件（预估）：
- 新增：`app/services/profit.py`、`app/tasks/profit.py`、`app/api/v1/analytics.py`、`app/schemas/profit.py`
- 改动：`app/api/v1/agents.py`（加 profit-analysis 端点）、`app/api/v1/__init__.py`（挂 analytics 路由）、`app/tasks/maintenance.py`（registry 加 `profit.compute`）、`app/core/config.py`（旋钮）、`backend/README.md`
- **不改 schema/迁移**：复用既有 `order_items.profit`（可空，已存在）、`products.cost`、`orders.shipping_fee/platform_fee/ad_cost`。**订单级利润不新增列**，按明细聚合派生（理由见 §3、§5a#5）。

---

## 2. 为什么用这种逻辑跑（运行 / 数据流 / 编排的理由）

**为什么完全不用 LLM？**
利润是确定性数学：收入、成本、费用、利润率、盈亏平衡价都有唯一正确答案。用 LLM 算会引入不可复现、可能算错、还烧钱的风险。DESIGN §3.18 明确「确定性计算用 Python/SQL，LLM 只负责分类/文案/总结」。本阶段只做数字；project.md「Profit Analysis Agent」里的自然语言 `recommendation` 属 LLM 文案，**留待后续阶段**（见 §5）。

**为什么按 `order_items` 逐条算，而不是按整单一个数？**
一单可含多商品（Phase 2 已拆 `order/order_items`），每个商品成本不同、利润不同。逐条算才能支撑「商品维度利润分析」（`GET /analytics/product/{id}/profit`）与 Phase 7 日报的 `low_profit_products`。订单级利润 = 明细利润之和，无需单独列。

**为什么订单级费用要分摊到明细？**
`shipping_fee / platform_fee / ad_cost` 挂在**订单头**，但净利润要落到**每条明细**。必须把订单费用按某口径分摊到各明细：默认按**该明细收入占订单总收入的比例**分摊（收入越高摊得越多，最贴近真实归因）。分摊后逐条 `net = gross − 分摊费用`。

**为什么用 compute_profit 异步任务回写、而不在下单/导入时同步算？**
1）成本与费用会变（改商品成本、补录广告费），利润需要**可重算**；把计算独立成可重复触发的任务，比耦合进 CRUD 写路径更干净、可控。
2）shop 级重算可能涉及大量订单，放异步队列避免拖慢请求。3）复用 Phase 3 生命周期 → 幂等/重试/超时/孤儿回收/审计（`agent_tasks` 记 `duration_ms`、计算版本、处理条数）全部白拿，与 Phase 4/5 一致。
> 因此 `order_items.profit` 在被 compute 之前保持 `NULL`（= 「尚未计算」），不是 0。

**为什么读接口（analytics）同步、写（compute）异步？**
读是一次聚合查询，快、无副作用，适合请求内同步返回。写要扫订单、回写多行、记审计，且需可重试/可观测，走异步任务更稳。

**数据流：**
```
POST /agent/profit-analysis {shop_id, order_id?, force?=false}
  → 校验 shop 属当前 account（order_id 若给则校验属该 shop）
  → 建 agent_tasks(pending, task_type=profit.compute, shop_id,
       entity_type=order|shop, input_json={order_id?}) + 入队(default)
  → 返回业务任务 id
worker(default, compute_profit):
  claim_running
  _run（只读）：载入目标订单(至多 PROFIT_MAX_ORDERS_PER_RUN)+明细+各明细对应 product.cost
              （worker 侧二次校验都属本 shop）→ services/profit.compute_orders()
  _finalize（同一事务）：有成本明细 UPDATE order_items.profit（带 order∈shop 兜底）；
              缺成本明细 UPDATE profit=NULL；+ finalize_succeeded
              + output_json 记 {orders, items, total_net, skipped_no_cost, unallocated_fee}

GET /analytics/product/{product_id}/profit
  → 校验 product 属当前 account 的 shop
  → 聚合 order_items⨝orders（该 product）→ 返回 gross/net/margin/break_even + 计数
```

---

## 3. 为什么要这样 build 代码（结构 / 取舍 / 权衡的理由）

**为什么把公式抽到纯函数 `services/profit.py`（无 session、无 I/O）？**
纯函数最易测、可复现、与「事务/任务/HTTP」解耦：compute 任务和 analytics 读接口都调它，单元测试只喂数字断言结果，不需要 DB。这正是「确定性逻辑集中在 services」（DESIGN 行 157）的落地。

**为什么全程 `Decimal` + 最大余数法分摊？**
钱必须精确，禁止 `float`。按比例分摊会产生小数尾差（如 3 条均摊 10.00 → 3.33×3≠10.00）。用**最大余数法**：先各取整到分，再把剩余 1 分依次补给「余数最大」的明细，保证 `Σ分摊费用 == 订单费用`（不多扣/不少扣）。所有金额 `ROUND_HALF_UP` 到 2 位，口径与 `Money=DECIMAL(12,2)` 一致。

**为什么 `product.cost` 缺失时把该明细记为「无成本」而不是当 0？**
`order_items.product_id` 可空（历史/已删商品靠快照兜底），此时拿不到成本。成本当 0 会**虚高利润**，是危险的脏数据。决策：成本不可得的明细 **`profit` 写 `NULL`（= 未计算）并计入 `output_json.skipped_no_cost`**，不影响同单其它明细，不让整单失败。读接口聚合时**排除**这些明细并在响应里报 `excluded_no_cost` 计数（不静默漏算）。

**缺成本明细如何参与费用分摊？（关键，避免低估或丢费用）**
费用分摊的分母用**订单内全部明细的收入**（`sale_price × quantity` 恒已知，与成本无关）。缺成本明细照常按其收入占比**分得**它那一份费用，但因为成本未知**不写 `profit`**——它分到的费用记入 `output_json.unallocated_fee`（归该明细、保留审计）。这样：① 有成本明细只承担**自己**那份费用，不会因为「替缺成本明细兜底」而被压低利润（虚低）；② 订单费用既不丢失也不转嫁（`Σ有成本明细费用 + Σ缺成本明细 unallocated_fee == 订单三项费用`，全局守恒）；③ 写回 `order_items.profit` 之和 = 仅「有成本明细」的净利之和，与「未知部分被显式标注」一致，不假装算全了。

**为什么 `_finalize` 要对缺成本明细强制写 `NULL`？**
`order_items.profit` 可能残留导入值或上一版计算值。若某明细这次因商品解绑/删除/成本缺失而无法计算，仅「跳过不写」会让旧利润继续被 analytics 聚合 → 虚高脏写。故 `_finalize` 对本次 scope 内**所有缺成本明细显式 `UPDATE profit = NULL`**（与有成本明细的回写在同一事务），保证「无法计算 ⇒ 必为 NULL」。

**为什么订单级利润派生、不新增 `orders.profit` 列？**
利润是 `order_items.profit` 的导出量，再存一份订单级会产生**双写一致性**负担（明细变了订单列要同步）。聚合求和成本低且永远一致，故 Phase 6 **不加迁移**。（project.md 扁平 `orders` 表列了 `profit`，但我们已拆表，单一事实源放在明细更合理。）若后续日报对性能有要求，再评估加物化列。

**为什么复用 `_run`/`_finalize` + `finalize_succeeded` 模式？**
与 Phase 4/5 完全同构：`_run` 只读取数（不占事务/连接做慢活，虽然本阶段无外部调用，但保持一致的「读算分离」），`_finalize` 用 `side_effect` 在同一事务回写多行 + 标记成功（要么全提交要么全回滚）。不引入新机制，降低 Codex 审阅与维护成本。

**为什么 break_even_price 在服务层按定义算？**
盈亏平衡价 = 让该明细 `net_profit == 0` 的单价。按既定费用与成本反解：`break_even_unit_price = unit_cost + 分摊费用/quantity`（quantity>0）。这是确定性公式，放纯函数里和其它指标一起算、一起测。

---

## 4. 验收标准（Phase 6）

- 给某 shop 造若干订单（多明细、含 shipping/platform/ad 费用、商品有 cost），`POST /agent/profit-analysis {shop_id}` 返回任务 id；worker 跑完后各 `order_items.profit` 被正确填充，且 `Σ明细净利 == 订单收入 − 总成本 − 订单费用`（费用分摊无尾差）。
- `agent_tasks` 该任务 `succeeded`，`output_json` 记录 `orders/items/total_net/skipped_no_cost`、`duration_ms`、`PROFIT_CALC_VERSION`。
- `GET /analytics/product/{id}/profit` 返回该商品的 `units_sold/revenue/cogs/fees/gross_profit/net_profit/profit_margin/break_even_price` 及 `excluded_no_cost` 计数，数值与手算一致。
- **成本缺失**：`product_id` 为空或商品无成本的明细 → `profit` 保持 `NULL`、计入 `skipped_no_cost`，不影响同单其它明细，任务仍 `succeeded`；analytics 聚合排除之并在 `excluded_no_cost` 体现。
- **可重算**：改商品 cost 或订单 ad_cost 后，以 `force=true` 重跑 `profit-analysis`，`order_items.profit` 被覆盖为新值（确定性）；默认 `force=false` 重复提交命中同幂等键、不新建任务。
- **越权**：`shop_id`/`order_id`/`product_id` 不属当前 account → 404，不入队/不计算；worker 侧二次校验订单属本 shop。
- **边界**：订单总收入为 0（全免单）时费用分摊与 margin 不除零崩溃（按 §5a#2 退化口径处理）；`quantity==0` 的明细 break_even 不除零。
- **单元测试（钱与事务为重点，不能只靠人工验收）**须覆盖：
  1. 费用守恒（唯一对外口径）：`Σallocated_fee(有成本) + Σunallocated_fee(缺成本) == 订单三项费用`（含多明细、含尾差场景）；并单测内部恒等式 `Σitem_fee == 订单三项费用`（二者不可相加）；
  2. 最大余数法 tie-break 按 `order_item.id ASC` 的确定性（同输入恒同输出）；
  3. `Σrevenue==0` 退化为按 quantity 分摊；quantity 也全 0 时记 `unallocated_fee`；
  4. 缺成本明细：`profit` 必为 `NULL`、记 `skipped_no_cost`/`unallocated_fee`、不转嫁不丢失；旧值被强制清 `NULL`；
  5. `finalize_succeeded` 同事务：任一回写 rowcount 异常 → 整任务回滚、无半写；
  6. 幂等：默认 `force=false` 重复提交复用任务；`force=true` 新建任务；
  7. 越权：shop/order/product 不属当前 account 被拒。

---

## 5a. 实施时必须钉死（Codex 第 1 轮审阅要点）

1. **利润口径定义**（写进代码常量与 README，避免歧义）：
   - `unit_cost = product.cost`（计算时点快照）；`revenue_item = sale_price × quantity`；`cogs_item = unit_cost × quantity`。
   - `gross_profit_item = revenue_item − cogs_item`。
   - `fee_item = 按收入占比分摊的 (shipping_fee + platform_fee + ad_cost)`。
   - `net_profit_item = gross_profit_item − fee_item`，写入 `order_items.profit`（**存净利**）。
   - `profit_margin = net_profit / revenue`（revenue=0 → 记 `null`，不写 0、不除零）。
   - **单明细** `break_even_unit_price = unit_cost + fee_item / quantity`（quantity>0；=0 记 `null`）。
   - **商品级 analytics**（聚合多明细）：`break_even_unit_price = (Σcogs + Σallocated_fees) / Σunits_sold`，其中 Σ 只统计**有成本**明细（quantity>0）；`units_sold==0` → 记 `null`。不得含糊复用单明细公式。
2. **费用分摊：分母含全部明细、精确无漂移、缺成本不转嫁**（术语先钉死，避免守恒式歧义）：
   - **`item_fee`（内部分摊量，全体明细各一份）**：分摊**分母 = 订单内全部明细的收入之和**（收入恒已知，与成本无关）；每条 `item_fee = 三项费用 × revenue_item / Σrevenue`。`Decimal` 取整到分后用**最大余数法**补齐尾差 1 分。**内部恒等式（仅用于分摊明细，不可再与下方量相加）**：`Σitem_fee == 订单三项费用之和`。
   - **按成本有无把 `item_fee` 二分**：有成本明细的 `item_fee` 记为 `allocated_fee`（参与 `net = gross − allocated_fee` 并写入 `order_items.profit`）；缺成本明细的 `item_fee` 记为 `unallocated_fee`（写入 `output_json.unallocated_fee`，**不**写 profit）。**绝不**把缺成本明细的费用转嫁给有成本明细（否则虚低利润），也不丢失。
   - **守恒式（唯一对外口径）**：`Σallocated_fee(有成本) + Σunallocated_fee(缺成本) == 订单三项费用之和`。注意这与上面的 `Σitem_fee == 订单费用` 是**同一笔钱的两种切分**，二者**不可相加**。
   - `Σrevenue == 0`：退化为**按 quantity 占比**分摊（仍最大余数法、同样二分为 allocated/unallocated）；若 quantity 也全 0，则该订单费用整体记 `output_json.unallocated_fee` 并跳过分摊（不崩）。
   - **最大余数 tie-break**：余数相同时按 `order_item.id ASC` 决定补哪条，保证同一输入在任何 DB 返回顺序下结果**唯一确定**。
3. **成本缺失不脏写、且强制清旧值**：`product_id IS NULL` 或对应 product 不存在/无 cost 的明细 → 计入 `skipped_no_cost`，**不参与** analytics 聚合（聚合端报 `excluded_no_cost`），绝不把缺失成本当 0。
   - `_finalize` 须对本次 scope 内**所有缺成本明细显式 `UPDATE order_items.profit = NULL`**（与有成本明细回写同一事务），防止导入值/旧计算值残留被聚合成虚高。即「无法计算 ⇒ 必为 NULL」。
4. **事务边界**：一个 compute 任务内，某 shop 的所有目标明细回写 + `mark_succeeded` 在**同一事务**提交（`finalize_succeeded` 的 `side_effect`）；任一回写 `rowcount` 异常即 raise → 整任务回滚按失败处理（不留半写）。回写 `UPDATE` 带 `order_items.order_id ∈ 本 shop 的订单` 兜底防串写。
5. **不新增 `orders.profit` 列**：订单级/商店级利润一律按 `order_items.profit` 聚合派生（单一事实源在明细）。本阶段**无 Alembic 迁移**。如评审认为需物化订单利润，再单列决策。
6. **幂等与可重算（消除与 `force` 的语义冲突）**：
   - **默认 `force=false`（幂等）**：`idempotency_key = profit.compute:{shop_id}:{order_id|all}:{profit_calc_version}`。重复提交命中同键 → **复用现有任务、不新建、不重复入队**（Phase 3 `create_task` 的 IntegrityError→re-read）。这是默认路径，符合 Phase 3 幂等约定与 `agent_tasks.idempotency_key` 唯一约束。
   - **公式升级**：bump `profit_calc_version` → 幂等键变化 → 自然产生一次新计算。
   - **`force=true`（手动强制重算，如改了 cost/ad_cost 但未升版本）**：幂等键追加 `:{uuid4}` uniquifier → 新建任务（与 Phase 5 force 同思路）。
   - **并发写安全**：计算是确定性的，同一批 `order_items` 即便被两个 force 任务并发覆盖，写入值也相同（收敛，无脏写）；每条回写带 `order∈shop` 兜底（见 §5a#4），最后写者得到的也是同一结果。文档注明 force 仅供人工触发，不应高频并发。
7. **单次处理上限**：`PROFIT_MAX_ORDERS_PER_RUN` 限制一个任务扫的订单数，shop 级超量时分批（task 返回/记 `remaining` 或由后续 dispatcher 续跑），**不静默截断**。
8. **双侧越权校验**：API 侧校验 `shop_id/order_id/product_id` 归属当前 account；worker 侧再按 `agent_tasks.shop_id` 复核订单归属（防队列污染/历史脏数据）。
9. **金额类型**：全程 `Decimal`，禁用 `float`；统一 `ROUND_HALF_UP` 到 2 位，与 `Money=DECIMAL(12,2)` 对齐。

## 5. 明确不在本阶段做（留待后续）

- **LLM 利润建议文案**（project.md Agent 5 的 `recommendation`）：属 LLM 文案，留待后续 Profit Analysis（LLM）阶段；本阶段只产出确定性数字（可附**规则型**布尔标记如 `low_margin`，但不调模型）。
- **Daily Report Agent**（Phase 7）：消费本阶段的利润数据生成日报。
- **店铺级 `GET /analytics/shop/{id}/daily|report`** 聚合面板（随 Phase 7 一起做；本阶段仅交付商品级利润读接口）。
- **订单级利润物化列 / 物化视图**（如性能需要再评估）。
- **真实平台 API 拉取费用**（shipping/platform/ad 仍由导入数据提供）。
