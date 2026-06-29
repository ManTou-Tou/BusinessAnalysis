"""利润计算（确定性，非 LLM）—— 纯函数层。

本模块只做数学：输入一张订单（明细 + 订单级费用），输出每条明细的
gross/net/margin/break_even 与订单级汇总。**无 DB、无 I/O、无 session**，
便于单测与复现（DESIGN §3.18：确定性计算用 Python，LLM 不参与）。

口径（与 PHASE_6_PLAN.md §5a 对齐）：
- revenue_item = sale_price × quantity；cogs_item = unit_cost × quantity；
  gross = revenue − cogs。
- 订单三项费用（shipping+platform+ad）按**全体明细收入占比**分摊成每条
  `item_fee`（Σitem_fee == 订单费用，最大余数法、tie-break order_item_id ASC）。
- 按成本有无把 item_fee 二分：有成本 → allocated_fee（计入 net）；缺成本 →
  unallocated_fee（不写 profit、仅审计）。守恒：Σallocated + Σunallocated == 订单费用。
- 全程 Decimal，金额 ROUND_HALF_UP 到 2 位；margin 到 4 位。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")
_RATIO = Decimal("0.0001")
_ZERO = Decimal("0.00")


def q2(value: Decimal) -> Decimal:
    """金额量化到分（2 位，ROUND_HALF_UP）。"""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    """比率量化到 4 位（ROUND_HALF_UP）。"""
    return value.quantize(_RATIO, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ItemInput:
    order_item_id: int
    quantity: int
    sale_price: Decimal
    unit_cost: Decimal | None  # None = 成本缺失（product_id 为空/商品已删/无成本）


@dataclass(frozen=True)
class OrderInput:
    order_id: int
    shipping_fee: Decimal
    platform_fee: Decimal
    ad_cost: Decimal
    items: list[ItemInput]


@dataclass(frozen=True)
class ItemProfit:
    order_item_id: int
    quantity: int
    revenue: Decimal
    item_fee: Decimal  # 该明细分得的费用（内部全体口径，allocated 与 unallocated 之一）
    has_cost: bool
    # 有成本时填充，缺成本时为 None
    cogs: Decimal | None
    gross_profit: Decimal | None
    allocated_fee: Decimal
    unallocated_fee: Decimal
    net_profit: Decimal | None
    profit_margin: Decimal | None
    break_even_unit_price: Decimal | None


@dataclass(frozen=True)
class OrderProfit:
    order_id: int
    items: list[ItemProfit]
    total_fee: Decimal
    total_allocated_fee: Decimal  # Σ有成本明细 allocated_fee
    total_unallocated_fee: Decimal  # Σ缺成本明细 unallocated_fee（+ 无法分摊时的整单费用）
    total_net_profit: Decimal  # Σ有成本明细 net_profit
    skipped_no_cost: int


def _to_cents(amount: Decimal) -> int:
    """Decimal 金额转整数分（精确，避免后续比例运算的浮动）。"""
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def allocate_largest_remainder(
    total_cents: int, weights: list[tuple[int, int]]
) -> dict[int, int]:
    """把 total_cents 按 weights 分摊为整数分，最大余数法补齐，确保 Σ结果 == total_cents。

    weights: [(item_id, weight)]，weight >= 0。余数相同按 item_id ASC 决定补哪条
    （确定性 tie-break，不依赖输入/DB 返回顺序）。sum(weight)==0 或 total_cents==0
    时全部返回 0（由调用方按「无法分摊」处理）。
    """
    sum_w = sum(w for _, w in weights)
    if sum_w == 0 or total_cents == 0:
        return {iid: 0 for iid, _ in weights}

    base: dict[int, int] = {}
    remainders: list[tuple[int, int]] = []  # (remainder, item_id)
    allocated = 0
    for iid, w in weights:
        share, rem = divmod(total_cents * w, sum_w)
        base[iid] = share
        allocated += share
        remainders.append((rem, iid))

    leftover = total_cents - allocated  # 0 <= leftover < 明细数
    remainders.sort(key=lambda x: (-x[0], x[1]))  # 余数大优先，tie-break id ASC
    for i in range(leftover):
        base[remainders[i][1]] += 1
    return base


def compute_order_profit(order: OrderInput) -> OrderProfit:
    """对一张订单做确定性利润计算，返回逐明细 + 汇总结果。"""
    total_fee = q2(order.shipping_fee + order.platform_fee + order.ad_cost)
    total_cents = _to_cents(total_fee)

    revenues = {it.order_item_id: q2(it.sale_price * it.quantity) for it in order.items}
    total_revenue = sum(revenues.values(), _ZERO)

    # 正常按收入分摊；Σrevenue==0 退化为按 quantity；quantity 也全 0 则无法分摊
    if total_revenue > 0:
        weights = [(it.order_item_id, _to_cents(revenues[it.order_item_id])) for it in order.items]
    else:
        weights = [(it.order_item_id, max(it.quantity, 0)) for it in order.items]

    fee_cents = allocate_largest_remainder(total_cents, weights)
    distributed_cents = sum(fee_cents.values())
    # 全体权重为 0（既无收入也无数量）→ 整单费用无法分摊，记为订单级 unallocated
    whole_unallocated = q2(Decimal(total_cents - distributed_cents) / 100)

    items_out: list[ItemProfit] = []
    total_allocated = _ZERO
    total_unallocated = whole_unallocated
    total_net = _ZERO
    skipped = 0

    for it in order.items:
        rev = revenues[it.order_item_id]
        item_fee = q2(Decimal(fee_cents[it.order_item_id]) / 100)
        if it.unit_cost is not None:
            cogs = q2(it.unit_cost * it.quantity)
            gross = q2(rev - cogs)
            net = q2(gross - item_fee)
            margin = q4(net / rev) if rev > 0 else None
            break_even = (
                q2(it.unit_cost + item_fee / it.quantity) if it.quantity > 0 else None
            )
            total_allocated += item_fee
            total_net += net
            items_out.append(
                ItemProfit(
                    order_item_id=it.order_item_id,
                    quantity=it.quantity,
                    revenue=rev,
                    item_fee=item_fee,
                    has_cost=True,
                    cogs=cogs,
                    gross_profit=gross,
                    allocated_fee=item_fee,
                    unallocated_fee=_ZERO,
                    net_profit=net,
                    profit_margin=margin,
                    break_even_unit_price=break_even,
                )
            )
        else:
            # 缺成本：item_fee 记为 unallocated，不写 profit，不转嫁给有成本明细
            skipped += 1
            total_unallocated += item_fee
            items_out.append(
                ItemProfit(
                    order_item_id=it.order_item_id,
                    quantity=it.quantity,
                    revenue=rev,
                    item_fee=item_fee,
                    has_cost=False,
                    cogs=None,
                    gross_profit=None,
                    allocated_fee=_ZERO,
                    unallocated_fee=item_fee,
                    net_profit=None,
                    profit_margin=None,
                    break_even_unit_price=None,
                )
            )

    return OrderProfit(
        order_id=order.order_id,
        items=items_out,
        total_fee=total_fee,
        total_allocated_fee=q2(total_allocated),
        total_unallocated_fee=q2(total_unallocated),
        total_net_profit=q2(total_net),
        skipped_no_cost=skipped,
    )
