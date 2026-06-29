"""Phase 7 日报「选取/排名」纯函数单测（无 DB）。

覆盖 top_products 排序与 tie-break、low_profit 仅纳入完全覆盖且收入>0、margin 边界。
（DB 聚合、upsert、先删后插、source_task_id 守卫等需 MySQL，另列集成测试。）
运行：backend/ 下 `pytest tests/test_report_selection.py`
"""
from decimal import Decimal

from app.services.report import (
    ProductAgg,
    select_low_profit_products,
    select_top_products,
)


def _p(pid, name, units, revenue, profit, items, covered):
    return ProductAgg(
        product_id=pid,
        product_name=name,
        units_sold=units,
        revenue=Decimal(revenue),
        profit=Decimal(profit),
        items=items,
        covered_items=covered,
    )


def test_top_products_by_revenue_desc_tiebreak_id_asc():
    products = [
        _p(3, "C", 1, "100.00", "10.00", 1, 1),
        _p(1, "A", 1, "100.00", "10.00", 1, 1),  # 同营收，id 小者在前
        _p(2, "B", 1, "300.00", "10.00", 1, 1),
    ]
    top = select_top_products(products, 2)
    assert [p.product_id for p in top] == [2, 1]


def test_margin_none_when_not_fully_covered_or_zero_revenue():
    assert _p(1, "A", 2, "100.00", "10.00", 2, 1).margin() is None  # 部分覆盖
    assert _p(2, "B", 1, "0.00", "0.00", 1, 1).margin() is None  # 收入为 0
    assert _p(3, "C", 1, "100.00", "20.00", 1, 1).margin() == Decimal("0.2000")


def test_low_profit_excludes_partial_coverage_and_sorts_asc():
    products = [
        _p(1, "A", 1, "100.00", "5.00", 1, 1),   # margin 0.05 < 0.10 ✓
        _p(2, "B", 1, "100.00", "2.00", 1, 1),   # margin 0.02 < 0.10 ✓（更低，应在前）
        _p(3, "C", 1, "100.00", "50.00", 1, 1),  # margin 0.50，非低利润
        _p(4, "D", 2, "100.00", "1.00", 2, 1),   # 部分覆盖 → 不纳入（数据不足）
    ]
    low = select_low_profit_products(products, Decimal("0.10"), 5)
    assert [p.product_id for p in low] == [2, 1]  # 按 margin 升序，部分覆盖被排除


def test_low_profit_respects_top_n():
    products = [
        _p(1, "A", 1, "100.00", "1.00", 1, 1),
        _p(2, "B", 1, "100.00", "2.00", 1, 1),
        _p(3, "C", 1, "100.00", "3.00", 1, 1),
    ]
    low = select_low_profit_products(products, Decimal("0.10"), 2)
    assert [p.product_id for p in low] == [1, 2]
