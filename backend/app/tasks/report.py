"""日报任务（Phase 7）。

generate_daily_report（queue=llm）：确定性聚合 + LLM 仅写散文 →
  **同一事务**（finalize_succeeded 的 side_effect）：
    ① shop_daily_metrics upsert（单行，确定性、顺序无关）
    ② product_daily_metrics 先按 (shop,date) DELETE 再 INSERT 当日全集（无陈旧残留）
    ③ daily_reports 带 source_task_id 守卫的条件 upsert（旧任务不覆盖新报告）
    ④ 标记成功 + 记 model/prompt_version/token_usage/cost
LLM 调用在事务外（_run 关 session 后），失败 → retry/failed，三表均不落库。

dispatch_daily_reports（queue=default，Beat 每日触发）：遍历 shops，为「昨天(UTC)」逐店建任务。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.agents.daily_report import DailyReportAgent
from app.agents.llm import LLMResult
from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.lock import redis_lock
from app.models.agent_task import TaskStatus
from app.models.daily_metrics import DailyReport, ProductDailyMetric, ShopDailyMetric
from app.models.shop import Shop
from app.services import agent_task as svc
from app.services import report as report_svc
from app.tasks.base import run_lifecycle


@dataclass
class _ReportOutcome:
    facts: report_svc.ReportFacts
    sections: dict
    result: LLMResult
    prompt_version: str
    source_task_id: int


def _run(agent_task_id: int) -> _ReportOutcome:
    """只读聚合（关 session 后调 LLM）。worker 侧二次校验 shop 归属。"""
    agent = DailyReportAgent()
    db = SessionLocal()
    try:
        task = svc.get_by_id(db, agent_task_id)
        if task is None or task.shop_id is None:
            raise ValueError("agent_task missing or has no shop_id")
        shop_id = task.shop_id
        info = task.input_json or {}
        date_str = info.get("date")
        if not date_str:
            raise ValueError("no date in input_json")
        d = date.fromisoformat(date_str)
        if db.get(Shop, shop_id) is None:  # 二次校验：shop 存在
            raise ValueError("shop not found")

        facts = report_svc.aggregate(db, shop_id, d)
        sections = report_svc.build_sections(facts)
        agent_input = report_svc.build_agent_input(facts)
    finally:
        db.close()  # LLM 调用前关闭 session

    result = agent.run(agent_input)  # LLM，无打开的 DB 连接
    return _ReportOutcome(
        facts=facts,
        sections=sections,
        result=result,
        prompt_version=agent.prompt_version,
        source_task_id=agent_task_id,
    )


def _finalize(db, agent_task_id: int, outcome: _ReportOutcome, duration_ms: int) -> None:
    facts = outcome.facts
    parsed = outcome.result.parsed  # DailyReportProse（仅散文）

    def side_effect(session) -> None:
        tid = outcome.source_task_id  # = agent_task_id（单调递增，作版本序）

        # 串行化同店日报写入 + 「新任务赢」统一闸门：
        # 锁住 shops 行，使同 (shop) 的并发 finalize 串行（消除 product 先删后插的唯一键竞争）；
        # 再比对 shop_daily_metrics.source_task_id——若已有更新的任务写过，则三表一致跳过，
        # 避免「/report 是新版、/daily 或 product 是旧版」的不一致（§5a#6 扩展到指标表）。
        session.execute(
            select(Shop.id).where(Shop.id == facts.shop_id).with_for_update()
        ).one()
        existing = session.execute(
            select(ShopDailyMetric.source_task_id).where(
                ShopDailyMetric.shop_id == facts.shop_id,
                ShopDailyMetric.date == facts.date,
            )
        ).scalar_one_or_none()
        if existing is not None and existing > tid:
            return  # 已有更新任务写过：旧任务不覆盖（指标/商品/报告一致跳过）

        # 我是最新（或首个）。锁内串行 + 闸门已过 → 三表用普通 upsert / 先删后插一致写入。
        # ① shop_daily_metrics upsert
        sstmt = mysql_insert(ShopDailyMetric).values(
            shop_id=facts.shop_id,
            date=facts.date,
            orders=facts.orders,
            revenue=facts.revenue,
            profit=facts.profit,
            ad_spend=facts.ad_spend,
            items_total=facts.items_total,
            profit_items_covered=facts.profit_items_covered,
            source_task_id=tid,
        )
        session.execute(
            sstmt.on_duplicate_key_update(
                orders=sstmt.inserted.orders,
                revenue=sstmt.inserted.revenue,
                profit=sstmt.inserted.profit,
                ad_spend=sstmt.inserted.ad_spend,
                items_total=sstmt.inserted.items_total,
                profit_items_covered=sstmt.inserted.profit_items_covered,
                source_task_id=sstmt.inserted.source_task_id,
            )
        )

        # ② product_daily_metrics：先删后插当日全集（清除陈旧商品行；锁内无竞争）
        session.execute(
            delete(ProductDailyMetric).where(
                ProductDailyMetric.shop_id == facts.shop_id,
                ProductDailyMetric.date == facts.date,
            )
        )
        if facts.products:
            session.execute(
                insert(ProductDailyMetric),
                [
                    {
                        "product_id": p.product_id,
                        "shop_id": facts.shop_id,
                        "date": facts.date,
                        "units_sold": p.units_sold,
                        "revenue": p.revenue,
                        "profit": p.profit,
                    }
                    for p in facts.products
                ],
            )

        # ③ daily_reports upsert（含散文 + 确定性 sections + source_task_id）
        rstmt = mysql_insert(DailyReport).values(
            shop_id=facts.shop_id,
            date=facts.date,
            daily_summary=parsed.daily_summary,
            recommended_actions=parsed.recommended_actions,
            sections_json=outcome.sections,
            model=outcome.result.model,
            prompt_version=outcome.prompt_version,
            source_task_id=tid,
        )
        session.execute(
            rstmt.on_duplicate_key_update(
                daily_summary=rstmt.inserted.daily_summary,
                recommended_actions=rstmt.inserted.recommended_actions,
                sections_json=rstmt.inserted.sections_json,
                model=rstmt.inserted.model,
                prompt_version=rstmt.inserted.prompt_version,
                source_task_id=rstmt.inserted.source_task_id,
                generated_at=func.now(),
            )
        )

    svc.finalize_succeeded(
        db,
        agent_task_id,
        output_json={
            "shop_id": facts.shop_id,
            "date": facts.date.isoformat(),
            "orders": facts.orders,
            "revenue": str(facts.revenue),
            "profit": str(facts.profit),
            "items_total": facts.items_total,
            "profit_items_covered": facts.profit_items_covered,
            "negative_count": facts.negative_count,
        },
        duration_ms=duration_ms,
        token_usage=outcome.result.total_tokens,
        cost=outcome.result.cost,
        model=outcome.result.model,
        prompt_version=outcome.prompt_version,
        side_effect=side_effect,
    )


@celery_app.task(bind=True, name="app.tasks.report.generate_daily_report", max_retries=100)
def generate_daily_report_task(self, agent_task_id: int, payload: dict | None = None):
    run_lifecycle(
        self,
        agent_task_id,
        lambda _p: _run(agent_task_id),
        payload,
        success_handler=lambda db, tid, outcome, dur: _finalize(db, tid, outcome, dur),
    )
    return {"agent_task_id": agent_task_id}


@celery_app.task(name="app.tasks.report.dispatch_daily_reports")
def dispatch_daily_reports(date_str: str | None = None) -> dict:
    """Beat 每日派发：遍历 shops，为目标日（默认昨天 UTC）逐店建 generate_daily_report。

    幂等键 + upsert + source_task_id 守卫兜底重复调度；MVP 单实例（多实例锁延后）。
    """
    db = SessionLocal()
    try:
        d = (
            date.fromisoformat(date_str)
            if date_str
            else (datetime.now(timezone.utc).date() - timedelta(days=1))
        )
        # 分布式锁（Phase 8）：多实例 Beat 下减少重复派发的惊群。锁失效也不破坏正确性——
        # 幂等键 + upsert + source_task_id 守卫仍兜底（锁提速、幂等保正确）。
        with redis_lock(
            f"lock:dispatch_daily_reports:{d.isoformat()}", settings.dispatch_lock_ttl_s
        ) as acquired:
            if not acquired:
                return {"date": d.isoformat(), "skipped_locked": True}

            shops = db.execute(select(Shop.id, Shop.account_id)).all()
            enqueued = skipped = failed = 0
            for sid, acct in shops:
                idem = f"daily.report:{sid}:{d.isoformat()}:{settings.report_calc_version}"
                task, created = svc.create_task(
                    db,
                    task_type="daily.report",
                    queue_name="llm",
                    idempotency_key=idem,
                    account_id=acct,
                    shop_id=sid,
                    entity_type="shop",
                    input_json={"date": d.isoformat()},
                )
                if not created:
                    skipped += 1
                    continue
                try:
                    async_res = generate_daily_report_task.apply_async(args=[task.id])
                    svc.set_celery_id(db, task.id, async_res.id)
                    enqueued += 1
                except Exception as exc:  # noqa: BLE001 - 入队失败留可见记录
                    db.rollback()
                    svc.mark_failed(
                        db,
                        task.id,
                        error_type="EnqueueError",
                        error_message=str(exc),
                        error_detail="",
                        from_statuses=(TaskStatus.PENDING,),
                    )
                    failed += 1
            return {
                "date": d.isoformat(),
                "shops": len(shops),
                "enqueued": enqueued,
                "skipped": skipped,
                "failed": failed,
            }
    finally:
        db.close()
