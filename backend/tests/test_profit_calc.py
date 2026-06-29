"""Phase 6 利润计算纯函数单测（无 DB）。

覆盖 PHASE_6_PLAN.md §4 验收里可纯函数验证的项：费用守恒、最大余数 tie-break、
零收入退化、缺成本不转嫁/不丢失、break_even/margin 公式与除零边界。
（finalize 同事务回滚、幂等、越权等 DB 集成测试需 MySQL，另列集成测试。）

运行：在 backend/ 下 `pytest tests/test_profit_calc.py`
"""
from decimal import Decimal

from app.services.profit import (
    ItemInput,
    OrderInput,
    allocate_largest_remainder,
    compute_order_profit,
)


def _d(v: str) -> Decimal:
    return Decimal(v)


def _order(items, shipping="0", platform="0", ad="0", order_id=1):
    return OrderInput(
        order_id=order_id,
        shipping_fee=_d(shipping),
        platform_fee=_d(platform),
        ad_cost=_d(ad),
        items=items,
    )


# ----------------------------- 最大余数法 -----------------------------


def test_largest_remainder_sums_exactly():
    out = allocate_largest_remainder(1000, [(1, 10000), (2, 10000), (3, 10000)])
    assert sum(out.values()) == 1000  # 守恒：无尾差


def test_largest_remainder_tiebreak_lowest_id_first():
    # 10 分摊给 3 个等权 → base 3 each(=9)，余 1 分按余数相同→id ASC 给最小 id
    out = allocate_largest_remainder(10, [(2, 1), (1, 1), (3, 1)])
    assert out == {1: 4, 2: 3, 3: 3}
    assert sum(out.values()) == 10


def test_largest_remainder_zero_weight_returns_zeros():
    out = allocate_largest_remainder(500, [(1, 0), (2, 0)])
    assert out == {1: 0, 2: 0}


# ----------------------------- 费用守恒 -----------------------------


def test_fee_conservation_all_costed():
    items = [
        ItemInput(order_item_id=1, quantity=1, sale_price=_d("3.34"), unit_cost=_d("1.00")),
        ItemInput(order_item_id=2, quantity=1, sale_price=_d("3.33"), unit_cost=_d("1.00")),
        ItemInput(order_item_id=3, quantity=1, sale_price=_d("3.33"), unit_cost=_d("1.00")),
    ]
    res = compute_order_profit(_order(items, shipping="10.00"))
    # 守恒式（对外唯一口径）：Σallocated + Σunallocated == 订单费用
    assert res.total_allocated_fee + res.total_unallocated_fee == _d("10.00")
    assert res.total_unallocated_fee == _d("0.00")
    # 内部恒等式：Σitem_fee == 订单费用
    assert sum((it.item_fee for it in res.items), Decimal("0")) == _d("10.00")


def test_fee_conservation_with_missing_cost_not_transferred():
    # 明细2 缺成本：它分得的费用进 unallocated，不转嫁给明细1，也不丢失
    items = [
        ItemInput(order_item_id=1, quantity=1, sale_price=_d("100.00"), unit_cost=_d("40.00")),
        ItemInput(order_item_id=2, quantity=1, sale_price=_d("100.00"), unit_cost=None),
    ]
    res = compute_order_profit(_order(items, platform="20.00"))
    i1 = next(i for i in res.items if i.order_item_id == 1)
    i2 = next(i for i in res.items if i.order_item_id == 2)
    # 各按收入占比分 10.00
    assert i1.allocated_fee == _d("10.00")
    assert i2.unallocated_fee == _d("10.00")
    assert i2.net_profit is None and i2.has_cost is False
    # 有成本明细只承担自己那份（不被缺成本明细兜底压低）
    assert i1.net_profit == _d("100.00") - _d("40.00") - _d("10.00")  # 50.00
    # 守恒
    assert res.total_allocated_fee + res.total_unallocated_fee == _d("20.00")
    assert res.skipped_no_cost == 1


# ----------------------------- 零收入 / 边界 -----------------------------


def test_zero_revenue_degrades_to_quantity():
    items = [
        ItemInput(order_item_id=1, quantity=3, sale_price=_d("0.00"), unit_cost=_d("1.00")),
        ItemInput(order_item_id=2, quantity=1, sale_price=_d("0.00"), unit_cost=_d("1.00")),
    ]
    res = compute_order_profit(_order(items, ad="4.00"))
    i1 = next(i for i in res.items if i.order_item_id == 1)
    i2 = next(i for i in res.items if i.order_item_id == 2)
    # 收入全 0 → 退化按 quantity(3:1) 分 4.00 → 3.00 / 1.00
    assert i1.allocated_fee == _d("3.00")
    assert i2.allocated_fee == _d("1.00")
    assert res.total_allocated_fee + res.total_unallocated_fee == _d("4.00")


def test_zero_revenue_and_zero_quantity_whole_unallocated():
    items = [
        ItemInput(order_item_id=1, quantity=0, sale_price=_d("0.00"), unit_cost=_d("1.00")),
    ]
    res = compute_order_profit(_order(items, shipping="5.00"))
    # 既无收入也无数量 → 整单费用无法分摊，记订单级 unallocated
    assert res.total_unallocated_fee == _d("5.00")
    assert res.total_allocated_fee == _d("0.00")
    assert res.items[0].allocated_fee == _d("0.00")


def test_margin_none_when_revenue_zero():
    items = [
        ItemInput(order_item_id=1, quantity=1, sale_price=_d("0.00"), unit_cost=_d("1.00")),
    ]
    res = compute_order_profit(_order(items))
    assert res.items[0].profit_margin is None


def test_break_even_none_when_quantity_zero():
    items = [
        ItemInput(order_item_id=1, quantity=0, sale_price=_d("0.00"), unit_cost=_d("1.00")),
    ]
    res = compute_order_profit(_order(items, shipping="5.00"))
    assert res.items[0].break_even_unit_price is None


# ----------------------------- 公式正确性 -----------------------------


def test_gross_net_margin_breakeven_formula():
    items = [
        ItemInput(order_item_id=1, quantity=2, sale_price=_d("50.00"), unit_cost=_d("30.00")),
    ]
    # 单明细独占全部费用 20.00（shipping10+platform6+ad4）
    res = compute_order_profit(_order(items, shipping="10.00", platform="6.00", ad="4.00"))
    it = res.items[0]
    assert it.revenue == _d("100.00")
    assert it.cogs == _d("60.00")
    assert it.gross_profit == _d("40.00")
    assert it.allocated_fee == _d("20.00")
    assert it.net_profit == _d("20.00")
    assert it.profit_margin == _d("0.2000")  # 20 / 100
    # break_even_unit = unit_cost + fee/quantity = 30 + 20/2 = 40
    assert it.break_even_unit_price == _d("40.00")


def test_determinism_independent_of_item_order():
    items_a = [
        ItemInput(order_item_id=1, quantity=1, sale_price=_d("3.34"), unit_cost=_d("1.00")),
        ItemInput(order_item_id=2, quantity=1, sale_price=_d("3.33"), unit_cost=_d("1.00")),
        ItemInput(order_item_id=3, quantity=1, sale_price=_d("3.33"), unit_cost=_d("1.00")),
    ]
    items_b = list(reversed(items_a))
    fees_a = {i.order_item_id: i.item_fee for i in compute_order_profit(_order(items_a, shipping="10.00")).items}
    fees_b = {i.order_item_id: i.item_fee for i in compute_order_profit(_order(items_b, shipping="10.00")).items}
    assert fees_a == fees_b  # 与输入顺序无关，结果唯一确定
