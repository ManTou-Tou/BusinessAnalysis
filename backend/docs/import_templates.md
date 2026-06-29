# Excel 导入模板（Phase 9）

上传 `.xlsx`，**首行为表头**（列名大小写/空格不敏感）。未知列忽略；缺**必填列**整文件拒绝；
坏行进错误报告、好行照常入库。一次最多 `IMPORT_MAX_FILE_BYTES`（默认 20MB）。

> 接口：`POST /api/v1/imports/{entity}`（multipart，字段 `file`；非 shops 需表单 `shop_id`；
> 可选 `conflict`、`force`）。需 `X-Account-Id` 头。状态/结果查 `GET /api/v1/agent/tasks/{id}`
> 的 `output_json`（含 `inserted/updated/error_count/errors[]/processed_rows`）。

## 冲突策略

- **products / orders / shops**：`upsert`（按唯一键更新或插入）。
- **reviews**：`insert`（append-only，无唯一键；传 `conflict=upsert` 报 400）。

## products（`POST /imports/products`，需 shop_id）

| 列 | 必填 | 说明 |
| --- | --- | --- |
| sku | ✓ | 唯一键（与 shop_id 组合）；已存在则更新 |
| product_name | ✓ | |
| price | ✓ | 金额，两位小数 |
| cost | ✓ | 金额 |
| stock | | 整数，默认 0 |
| status | | active/inactive/out_of_stock，默认 active |
| category | | |

## orders（`POST /imports/orders`，需 shop_id）

**一行 = 一条订单明细**；同一 `order_no` 的明细行**必须相邻**（非相邻重复 → 整文件失败）。
订单头字段取该订单首行；重导按 order_no upsert 订单头 + 明细先删后插。`profit` 不在导入写
（由利润计算另算）。

| 列 | 必填 | 说明 |
| --- | --- | --- |
| order_no | ✓ | 订单唯一键（与 shop_id 组合） |
| quantity | ✓ | 明细数量，整数 |
| sale_price | ✓ | 明细单价，金额 |
| product_sku | | 解析为 product_id（找不到留快照、product_id 置空） |
| order_status | | 订单头，默认 pending |
| total_amount / shipping_fee / platform_fee / ad_cost | | 订单头金额，默认 0 |

## reviews（`POST /imports/reviews`，需 shop_id）

append-only。崩溃/重试**断点续跑**（已导入行不重复）。

| 列 | 必填 | 说明 |
| --- | --- | --- |
| product_sku | ✓ | 解析为本店商品（找不到 → 行错误） |
| review_text | ✓ | |
| order_no | | 解析为本店订单（给了却找不到 → 行错误） |
| buyer_name | | |
| rating | | 1–5 整数 |

## shops（`POST /imports/shops`，account 级，无需 shop_id）

按 `(account_id, external_shop_id)` upsert。

| 列 | 必填 | 说明 |
| --- | --- | --- |
| external_shop_id | ✓ | 唯一键（必填非空） |
| platform | ✓ | shopee/tiktok_shop/lazada |
| shop_name | ✓ | |
| status | | 默认 active |
