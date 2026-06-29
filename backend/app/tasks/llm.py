"""LLM 队列任务（queue=llm）。

classify_review（单条，Phase 4）/ classify_reviews_batch（批量，Phase 5）：跑对应 Agent，
成功后**原子地**把分类写回 reviews 并把成本/用量/模型/prompt 版本记入 agent_tasks（同一事务）。
worker 侧二次校验租户归属。批量按本批 index 对齐，行级未命中按 skipped 处理（不毒性重试）。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update

from app.agents.llm import LLMResult
from app.agents.review_batch_classifier import ReviewBatchClassifierAgent
from app.agents.review_classifier import ReviewClassifierAgent
from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.db import SessionLocal
from app.models.agent_task import AgentTask
from app.models.review import Review
from app.services import agent_task as svc
from app.tasks.base import run_lifecycle


@dataclass
class _ClassifyOutcome:
    review_id: int
    shop_id: int
    result: LLMResult
    prompt_version: str


def _run(agent_task_id: int) -> _ClassifyOutcome:
    """读任务与评论（worker 侧二次校验归属），调 Agent。只读，不写。"""
    agent = ReviewClassifierAgent()
    db = SessionLocal()
    try:
        task = svc.get_by_id(db, agent_task_id)
        if task is None:
            raise ValueError("agent_task not found")
        review = db.get(Review, task.entity_id) if task.entity_id else None
        # 二次越权校验：评论必须存在且属于任务记录的 shop（防队列污染/脏数据）
        if review is None or task.shop_id is None or review.shop_id != task.shop_id:
            raise ValueError("review not found or tenant mismatch")
        result = agent.run({"review_text": review.review_text})
        return _ClassifyOutcome(
            review_id=review.id,
            shop_id=task.shop_id,
            result=result,
            prompt_version=agent.prompt_version,
        )
    finally:
        db.close()


def _finalize(db, agent_task_id: int, outcome: _ClassifyOutcome, duration_ms: int) -> None:
    parsed = outcome.result.parsed

    def write_review(session) -> None:
        # 同事务回写业务字段；限定 shop_id 兜底防串写，并要求恰好命中 1 行，
        # 否则 raise → run_lifecycle 回滚并按失败处理（不会留下 succeeded 但未回写）。
        res = session.execute(
            update(Review)
            .where(Review.id == outcome.review_id, Review.shop_id == outcome.shop_id)
            .values(review_type=parsed.review_type, sentiment=parsed.sentiment)
        )
        if res.rowcount != 1:
            raise ValueError(
                f"review {outcome.review_id} not updated (rowcount={res.rowcount})"
            )

    svc.finalize_succeeded(
        db,
        agent_task_id,
        output_json=parsed.model_dump(),  # 含 summary/need_reply，仅入 agent_tasks
        duration_ms=duration_ms,
        token_usage=outcome.result.total_tokens,
        cost=outcome.result.cost,
        model=outcome.result.model,
        prompt_version=outcome.prompt_version,
        side_effect=write_review,
    )


@celery_app.task(bind=True, name="app.tasks.llm.classify_review", max_retries=100)
def classify_review_task(self, agent_task_id: int, payload: dict | None = None):
    # run_lifecycle 的返回是内部 outcome 对象（不可 JSON 序列化）—— 不作为 Celery 结果返回；
    # 这里只返回一个 JSON 安全的小 dict。
    run_lifecycle(
        self,
        agent_task_id,
        lambda _p: _run(agent_task_id),
        payload,
        success_handler=lambda db, tid, outcome, dur: _finalize(db, tid, outcome, dur),
    )
    return {"agent_task_id": agent_task_id}


# ----------------------------- 批量分类（Phase 5） -----------------------------


@dataclass
class _BatchOutcome:
    shop_id: int
    force: bool
    # (index, review_id, ReviewClassificationItem)
    items: list[tuple]
    missing_ids: list[int]  # 请求了但查不到（已删/跨店铺）→ 读阶段即 skipped
    result: LLMResult | None  # 工作集为空时为 None（无 LLM 调用）
    prompt_version: str


def _run_batch(agent_task_id: int) -> _BatchOutcome:
    """读该块 review_ids（worker 侧按 shop 二次校验）后**关闭 session**，再调批量 Agent。

    LLM 调用不在任何打开的 DB 事务/连接内（先把需要的数据读出来再调）。只读，不写库。
    """
    agent = ReviewBatchClassifierAgent()
    db = SessionLocal()
    try:
        task = svc.get_by_id(db, agent_task_id)
        if task is None or task.shop_id is None:
            raise ValueError("agent_task missing or has no shop_id")
        info = task.input_json or {}
        review_ids = info.get("review_ids") or []
        force = bool(info.get("force"))
        if not review_ids:
            raise ValueError("no review_ids in input_json")
        shop_id = task.shop_id

        rows = db.execute(
            select(Review).where(
                Review.shop_id == shop_id, Review.id.in_(review_ids)
            )
        ).scalars().all()
        found = {r.id: r for r in rows}
        # 保持请求顺序；查不到的记为 missing（已删/跨店铺）。截断文本，捕获为普通元组。
        working = [
            (rid, (found[rid].review_text or "")[: settings.review_text_max_chars])
            for rid in review_ids
            if rid in found
        ]
        missing_ids = [rid for rid in review_ids if rid not in found]
    finally:
        db.close()  # LLM 调用前关闭 session

    if not working:
        # 全部缺失：无需调用 LLM
        return _BatchOutcome(
            shop_id=shop_id,
            force=force,
            items=[],
            missing_ids=missing_ids,
            result=None,
            prompt_version=agent.prompt_version,
        )

    indexed = [(i + 1, rid, text) for i, (rid, text) in enumerate(working)]
    payload_reviews = [{"index": i, "text": text} for i, _rid, text in indexed]
    result = agent.run({"reviews": payload_reviews})  # LLM 调用，无打开的 DB 连接
    out = result.parsed.items

    # 强制校验：输出条数 == 输入条数，index 覆盖 1..N 无重复无缺失
    got = sorted(it.index for it in out)
    expected = list(range(1, len(indexed) + 1))
    if got != expected:
        raise ValueError(
            f"batch output index mismatch: expected {expected}, got {got}"
        )
    by_index = {it.index: it for it in out}
    items = [(i, rid, by_index[i]) for i, rid, _text in indexed]
    return _BatchOutcome(
        shop_id=shop_id,
        force=force,
        items=items,
        missing_ids=missing_ids,
        result=result,
        prompt_version=agent.prompt_version,
    )


def _finalize_batch(db, agent_task_id: int, outcome: _BatchOutcome, duration_ms: int) -> None:
    def side_effect(session) -> None:
        classified: list[dict] = []
        skipped: list[dict] = []
        for _index, review_id, cls in outcome.items:
            entry = {
                "review_id": review_id,
                "review_type": cls.review_type,
                "sentiment": cls.sentiment,
                "summary": cls.summary,
                "need_reply": cls.need_reply,
            }
            stmt = update(Review).where(
                Review.id == review_id, Review.shop_id == outcome.shop_id
            )
            if not outcome.force:
                # only_unclassified：不覆盖期间已被并发分类的行
                stmt = stmt.where(Review.review_type.is_(None))
            res = session.execute(
                stmt.values(review_type=cls.review_type, sentiment=cls.sentiment)
            )
            if res.rowcount == 1:
                classified.append(entry)
            else:
                # 行级未命中（已被并发分类/已删）→ skipped，保留 LLM 输出 + 原因，不失败/不毒性重试
                skipped.append({**entry, "reason": "already_classified_or_deleted"})
        # 读阶段缺失（已删/跨店铺）：无 LLM 输出，仅记 id + 原因
        for rid in outcome.missing_ids:
            skipped.append({"review_id": rid, "reason": "missing"})
        # 同事务内写 output_json（含逐条结果 + skipped，保留审计价值）
        session.execute(
            update(AgentTask)
            .where(AgentTask.id == agent_task_id)
            .values(output_json={"classified": classified, "skipped": skipped})
        )

    result = outcome.result
    svc.finalize_succeeded(
        db,
        agent_task_id,
        output_json=None,  # 真正的 output_json 由 side_effect 在同事务内写入
        duration_ms=duration_ms,
        token_usage=result.total_tokens if result else None,
        cost=result.cost if result else None,
        model=result.model if result else None,
        prompt_version=outcome.prompt_version,
        side_effect=side_effect,
    )


@celery_app.task(
    bind=True,
    name="app.tasks.llm.classify_reviews_batch",
    max_retries=100,
    rate_limit=settings.llm_classify_rate_limit,
)
def classify_reviews_batch_task(self, agent_task_id: int, payload: dict | None = None):
    run_lifecycle(
        self,
        agent_task_id,
        lambda _p: _run_batch(agent_task_id),
        payload,
        success_handler=lambda db, tid, outcome, dur: _finalize_batch(
            db, tid, outcome, dur
        ),
    )
    return {"agent_task_id": agent_task_id}
