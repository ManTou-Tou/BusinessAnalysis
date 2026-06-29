"""Profit 计算任务（queue=default，Phase 6）。

compute_profit：确定性重算某 shop（或单订单）的利润，**原子地**把每条
order_items.profit 回写并把汇总记入 agent_tasks（同一事务）。不调 LLM。

读/算/写分离（与 Phase 4/5 同构）：
- `_run`：只读载入订单 + 明细 + 商品成本（worker 侧二次校验归属），关闭 session 后做纯计算；
- `_finalize`：在 `finalize_succeeded` 的 side_effect 内同事务回写：有成本明细写 net_profit、
  缺成本明细强制 `profit=NULL`（清旧值），并写汇总 output_json。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, update

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.db import SessionLocal
from app.models.agent_task import AgentTask
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.services import agent_task as svc
from app.services.profit import ItemInput, OrderInput, OrderProfit, compute_order_profit, q2
from app.tasks.base import run_lifecycle

_ZERO = Decimal("0.00")


@dataclass
class _ComputeOutcome:
    shop_id: int
    orders: list[OrderProfit]


def _run(agent_task_id: int) -> _ComputeOutcome:
    """只读载入目标订单与成本（worker 侧二次校验归属），关闭 session 后纯计算。"""
    db = SessionLocal()
    try:
        task = svc.get_by_id(db, agent_task_id)
        if task is None or task.shop_id is None:
            raise ValueError("agent_task missing or has no shop_id")
        shop_id = task.shop_id
        info = task.input_json or {}
        order_id = info.get("order_id")

        conds = [Order.shop_id == shop_id]
        if order_id is not None:
            conds.append(Order.id == order_id)

        total_orders = db.execute(
            select(func.count()).select_from(Order).where(*conds)
        ).scalar_one()
        # 不静默截断：scope 超过单次上限即整任务失败（API 已先行拦截；此处为防御性二次校验，
        # 避免经 reaper 重投等旁路创建的超量任务半量成功后被幂等键挡住、剩余订单永不计算）。
        if order_id is None and total_orders > settings.profit_max_orders_per_run:
            raise ValueError(
                f"shop {shop_id} has {total_orders} orders exceeding "
                f"PROFIT_MAX_ORDERS_PER_RUN={settings.profit_max_orders_per_run}; "
                "narrow scope (order_id) or raise the limit"
            )
        orders = list(
            db.execute(
                select(Order).where(*conds).order_by(Order.id)
            ).scalars().all()
        )
        # 单订单 scope 但查不到 → 越权/不存在（双侧校验）
        if order_id is not None and not orders:
            raise ValueError("order not found or tenant mismatch")

        order_ids = [o.id for o in orders]
        items = (
            list(
                db.execute(
                    select(OrderItem).where(OrderItem.order_id.in_(order_ids))
                ).scalars().all()
            )
            if order_ids
            else []
        )
        product_ids = {it.product_id for it in items if it.product_id is not None}
        costs: dict[int, Decimal] = {}
        if product_ids:
            for pid, cost in db.execute(
                select(Product.id, Product.cost).where(
                    Product.shop_id == shop_id, Product.id.in_(product_ids)
                )
            ):
                costs[pid] = cost

        items_by_order: dict[int, list[OrderItem]] = defaultdict(list)
        for it in items:
            items_by_order[it.order_id].append(it)

        order_inputs = [
            OrderInput(
                order_id=o.id,
                shipping_fee=o.shipping_fee,
                platform_fee=o.platform_fee,
                ad_cost=o.ad_cost,
                items=[
                    ItemInput(
                        order_item_id=it.id,
                        quantity=it.quantity,
                        sale_price=it.sale_price,
                        # product_id 为空或商品查不到 → 成本缺失
                        unit_cost=costs.get(it.product_id)
                        if it.product_id is not None
                        else None,
                    )
                    for it in items_by_order.get(o.id, [])
                ],
            )
            for o in orders
        ]
    finally:
        db.close()  # 纯计算前关闭 session（保持读/算/写分离）

    results = [compute_order_profit(oi) for oi in order_inputs]
    return _ComputeOutcome(shop_id=shop_id, orders=results)


def _finalize(db, agent_task_id: int, outcome: _ComputeOutcome, duration_ms: int) -> None:
    def side_effect(session) -> None:
        # 回写兜底：明细所属订单必须属本 shop（防队列污染/串写）
        owned_orders = select(Order.id).where(Order.shop_id == outcome.shop_id)

        no_cost_ids: list[int] = []
        total_net = _ZERO
        total_unallocated = _ZERO
        n_items = 0
        n_skipped = 0

        for od in outcome.orders:
            total_unallocated += od.total_unallocated_fee
            for ip in od.items:
                n_items += 1
                if ip.has_cost:
                    res = session.execute(
                        update(OrderItem)
                        .where(
                            OrderItem.id == ip.order_item_id,
                            OrderItem.order_id.in_(owned_orders),
                        )
                        .values(profit=ip.net_profit)
                    )
                    if res.rowcount != 1:
                        # 真异常（明细消失/串写）→ raise，整任务回滚按失败处理，不半写
                        raise ValueError(
                            f"order_item {ip.order_item_id} not updated "
                            f"(rowcount={res.rowcount})"
                        )
                    total_net += ip.net_profit
                else:
                    no_cost_ids.append(ip.order_item_id)
                    n_skipped += 1

        # 缺成本明细：强制清旧值为 NULL（防导入值/上一版计算值残留被聚合成虚高）。
        # 一条批量 UPDATE；FOUND_ROWS 下 rowcount==匹配行数，必须等于目标数，
        # 否则说明 _run 后有明细被删/改归属（真异常）→ raise 回滚，不留半写脏值。
        if no_cost_ids:
            res = session.execute(
                update(OrderItem)
                .where(
                    OrderItem.id.in_(no_cost_ids),
                    OrderItem.order_id.in_(owned_orders),
                )
                .values(profit=None)
            )
            if res.rowcount != len(no_cost_ids):
                raise ValueError(
                    f"no-cost NULL-out matched {res.rowcount} rows, "
                    f"expected {len(no_cost_ids)}"
                )

        session.execute(
            update(AgentTask)
            .where(AgentTask.id == agent_task_id)
            .values(
                output_json={
                    "orders": len(outcome.orders),
                    "items": n_items,
                    "total_net": str(q2(total_net)),
                    "skipped_no_cost": n_skipped,
                    "unallocated_fee": str(q2(total_unallocated)),
                    "calc_version": settings.profit_calc_version,
                }
            )
        )

    svc.finalize_succeeded(
        db,
        agent_task_id,
        output_json=None,  # 真正的 output_json 由 side_effect 在同事务内写入
        duration_ms=duration_ms,
        prompt_version=settings.profit_calc_version,  # 复用审计字段记口径版本
        side_effect=side_effect,
    )


@celery_app.task(bind=True, name="app.tasks.profit.compute_profit", max_retries=100)
def compute_profit_task(self, agent_task_id: int, payload: dict | None = None):
    run_lifecycle(
        self,
        agent_task_id,
        lambda _p: _run(agent_task_id),
        payload,
        success_handler=lambda db, tid, outcome, dur: _finalize(db, tid, outcome, dur),
    )
    return {"agent_task_id": agent_task_id}
