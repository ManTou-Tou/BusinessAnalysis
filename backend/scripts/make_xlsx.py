"""生成冒烟测试用的 .xlsx 文件（products / orders / reviews）。

列模板严格对齐 backend/docs/import_templates.md：首行表头，列名大小写/空格不敏感。
故意混入少量「坏行」以验证「坏行进错误报告、好行照常入库」的行为。

用法（在 backend/ 目录、已激活 venv）：
    python scripts/make_xlsx.py

输出到 backend/scripts/sample_data/{products,orders,reviews}.xlsx
"""
from __future__ import annotations

import os

from openpyxl import Workbook

OUT_DIR = os.path.join(os.path.dirname(__file__), "sample_data")

# products.xlsx 里的 SKU，orders/reviews 引用它们解析 product_id。
SKUS = ["SKU-001", "SKU-002", "SKU-003", "SKU-004", "SKU-005"]


def _save(name: str, header: list[str], rows: list[list]) -> str:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    path = os.path.join(OUT_DIR, name)
    wb.save(path)
    return path


def make_products() -> str:
    header = ["sku", "product_name", "price", "cost", "stock", "status", "category"]
    rows = [
        ["SKU-001", "无线蓝牙耳机", 199.00, 80.00, 50, "active", "电子"],
        ["SKU-002", "硅胶手机壳", 29.90, 5.50, 200, "active", "配件"],
        ["SKU-003", "快充数据线", 39.00, 9.00, 150, "active", "配件"],
        ["SKU-004", "桌面收纳盒", 59.90, 22.00, 80, "inactive", "家居"],
        ["SKU-005", "便携充电宝", 129.00, 55.00, 0, "out_of_stock", "电子"],
        # 坏行：price 非数字 → 进 errors[]，不影响其它行入库。
        ["SKU-BAD", "坏行-价格非法", "not_a_number", 10.00, 5, "active", "测试"],
    ]
    return _save("products.xlsx", header, rows)


def make_orders() -> str:
    # 一行 = 一条订单明细；同一 order_no 的明细必须相邻。
    header = [
        "order_no", "quantity", "sale_price", "product_sku", "order_status",
        "total_amount", "shipping_fee", "platform_fee", "ad_cost",
    ]
    rows = [
        # 单品订单
        ["ORD-1001", 1, 199.00, "SKU-001", "completed", 205.00, 6.00, 12.00, 8.00],
        # 多品订单：两条明细相邻（合法）
        ["ORD-1002", 2, 29.90, "SKU-002", "paid", 110.80, 6.00, 8.00, 5.00],
        ["ORD-1002", 1, 39.00, "SKU-003", "paid", 110.80, 6.00, 8.00, 5.00],
        # 单品订单
        ["ORD-1003", 3, 59.90, "SKU-004", "shipped", 185.70, 8.00, 14.00, 10.00],
        ["ORD-1004", 1, 129.00, "SKU-005", "pending", 135.00, 6.00, 9.00, 0.00],
        # 引用不存在的 SKU：留快照、product_id 置空（不算坏行，按模板允许）
        ["ORD-1005", 1, 49.00, "SKU-UNKNOWN", "completed", 55.00, 6.00, 5.00, 3.00],
    ]
    return _save("orders.xlsx", header, rows)


def make_reviews() -> str:
    header = ["product_sku", "review_text", "order_no", "buyer_name", "rating"]
    rows = [
        ["SKU-001", "音质很好，物超所值！", "ORD-1001", "Alice", 5],
        ["SKU-001", "连接偶尔断开，有点失望。", "", "Bob", 2],
        ["SKU-002", "壳子贴合度不错。", "ORD-1002", "Carol", 4],
        ["SKU-003", "充电速度快。", "ORD-1002", "Dave", 5],
        ["SKU-004", "收纳盒做工一般。", "", "Eve", 3],
        ["SKU-005", "充电宝很重，但容量大。", "ORD-1004", "Frank", 4],
        ["SKU-002", "物流太慢了，等了两周。", "", "Grace", 2],
        ["SKU-001", "客服回复及时，点赞。", "", "Heidi", 5],
        # 坏行：product_sku 找不到 → 行错误。
        ["SKU-NOPE", "引用了不存在的商品", "", "Ivan", 3],
        # 坏行：review_text 必填为空 → 行错误。
        ["SKU-003", "", "", "Judy", 4],
    ]
    return _save("reviews.xlsx", header, rows)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for path in (make_products(), make_orders(), make_reviews()):
        print(f"saved {path}")


if __name__ == "__main__":
    main()
