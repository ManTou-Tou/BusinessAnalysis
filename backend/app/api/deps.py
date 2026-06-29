"""API 依赖：DB session、租户标识、分页参数。

MVP 阶段以 `X-Account-Id` 请求头标识租户（暂未接入正式鉴权）。
所有业务路由都必须经此依赖拿到 account_id，确保数据按租户隔离。
"""
from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.account import Account
from app.utils.pagination import DEFAULT_LIMIT, MAX_LIMIT


def require_dev_env() -> None:
    """限制仅非生产环境可用（用于尚未鉴权的开发/测试桩接口）。"""
    if settings.app_env == "production":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not found"
        )


def get_account_id(
    x_account_id: int = Header(..., alias="X-Account-Id"),
    db: Session = Depends(get_db),
) -> int:
    # 注意：MVP 阶段以 X-Account-Id 头标识租户，仅校验 account 是否存在，
    # 不做身份认证。属临时开发机制，正式鉴权（登录/Token）在后续阶段引入。
    exists = db.execute(
        select(Account.id).where(Account.id == x_account_id)
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not found"
        )
    return x_account_id


class PageParams:
    """分页查询参数（cursor + limit）。"""

    def __init__(
        self,
        cursor: int | None = Query(default=None, ge=1),
        limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    ) -> None:
        self.cursor = cursor
        self.limit = limit
