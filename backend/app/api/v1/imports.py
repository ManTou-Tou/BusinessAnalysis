"""Excel 导入上传路由（Phase 9）。

POST /imports/{entity}：校验（扩展名/ZIP 魔数/大小/归属）→ 落盘 → 建 sync 任务 → 返回任务 id。
解析入库在 worker（app.tasks.imports），状态/结果查 GET /agent/tasks/{id}。
"""
from __future__ import annotations

import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_account_id
from app.core.config import settings
from app.core.db import get_db
from app.core.ratelimit import RateLimit
from app.models.agent_task import TaskStatus
from app.schemas.agent_task import AgentTaskOut
from app.schemas.imports import IMPORT_ENTITIES
from app.services import agent_task as svc
from app.services.imports.common import MAGIC_ZIP
from app.services.tenant import get_owned_shop

router = APIRouter(prefix="/imports", tags=["imports"])

_READ_CHUNK = 1024 * 1024


@router.post(
    "/{entity}",
    response_model=AgentTaskOut,
    status_code=202,
    dependencies=[Depends(RateLimit("imports", limit=settings.ratelimit_llm_trigger_limit))],
)
async def import_excel(
    entity: str,
    file: UploadFile = File(...),
    shop_id: int | None = Form(default=None),
    conflict: str = Form(default="upsert"),
    force: bool = Form(default=False),
    account_id: int = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> AgentTaskOut:
    if entity not in IMPORT_ENTITIES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown import entity")

    # 冲突策略：reviews 仅 insert（append-only，无唯一键）；其它 upsert。
    if entity == "reviews":
        if conflict not in ("insert", "upsert"):
            raise HTTPException(status_code=400, detail="invalid conflict mode")
        if conflict == "upsert":
            raise HTTPException(
                status_code=400,
                detail="reviews import is append-only (insert); upsert not supported",
            )
        conflict = "insert"
    else:
        if conflict != "upsert":
            raise HTTPException(
                status_code=400, detail=f"{entity} import supports conflict=upsert only"
            )

    # 归属：非 shops 实体需 shop_id 且属当前 account；shops 为 account 级。
    if entity == "shops":
        shop_id = None
    else:
        if shop_id is None:
            raise HTTPException(status_code=400, detail=f"{entity} import requires shop_id")
        get_owned_shop(db, account_id, shop_id)

    # 扩展名校验。
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="only .xlsx files are supported")

    # 落盘（流式，限大小，算 sha256，校验 ZIP 魔数）。生成名防穿越/重名。
    os.makedirs(settings.import_upload_dir, exist_ok=True)
    dest = os.path.join(settings.import_upload_dir, f"{uuid.uuid4().hex}.xlsx")
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(_READ_CHUNK)
                if not chunk:
                    break
                if not head:
                    head = chunk[:4]
                size += len(chunk)
                if size > settings.import_max_file_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"file exceeds {settings.import_max_file_bytes} bytes",
                    )
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="empty file")
        if head[:4] != MAGIC_ZIP:
            raise HTTPException(status_code=400, detail="not a valid .xlsx (bad signature)")
    except HTTPException:
        _safe_remove(dest)
        raise
    except Exception:  # noqa: BLE001 - 落盘异常清理后抛出
        _safe_remove(dest)
        raise

    sha = digest.hexdigest()
    base_key = f"import.{entity}:{account_id}:{sha}"
    idem = base_key if not force else f"{base_key}:{uuid.uuid4().hex}"

    task, created = svc.create_task(
        db,
        task_type=f"import.{entity}",
        queue_name="sync",
        idempotency_key=idem,
        account_id=account_id,
        shop_id=shop_id,
        entity_type=entity,
        input_json={
            "path": dest,
            "entity": entity,
            "account_id": account_id,
            "shop_id": shop_id,
            "conflict": conflict,
        },
    )
    if not created:
        _safe_remove(dest)  # 幂等命中：本次落盘的文件为多余副本，清理
        return task

    try:
        from app.tasks.imports import run_import_task

        async_res = run_import_task.apply_async(args=[task.id])
        svc.set_celery_id(db, task.id, async_res.id)
    except Exception as exc:  # noqa: BLE001 - 入队失败留可见记录；**保留文件**（不删）
        # 不删上传文件：apply_async 可能已成功（任务已进 worker，只是 set_celery_id 失败），
        # 删文件会让 worker 找不到。失败/重试一律保留文件（与「成功才删」一致）；
        # 终态失败的残留文件由后续清理任务回收（见 PHASE_9_PLAN §5）。
        svc.mark_failed(
            db,
            task.id,
            error_type="EnqueueError",
            error_message=str(exc),
            error_detail="",
            from_statuses=(TaskStatus.PENDING,),
        )
    return svc.get_by_id(db, task.id)


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
