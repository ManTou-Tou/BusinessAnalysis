"""Account 路由（多租户管理桩，仅非生产环境可用，尚未鉴权）。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import PageParams, require_dev_env
from app.core.db import get_db
from app.schemas.account import AccountCreate, AccountOut
from app.schemas.common import Page
from app.services import account as account_service

# 整个 accounts 路由受 require_dev_env 保护：生产环境返回 404。
router = APIRouter(
    prefix="/accounts", tags=["accounts"], dependencies=[Depends(require_dev_env)]
)


@router.post("", response_model=AccountOut, status_code=201)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)) -> AccountOut:
    return account_service.create_account(db, payload)


@router.get("", response_model=Page[AccountOut])
def list_accounts(
    params: PageParams = Depends(), db: Session = Depends(get_db)
) -> Page[AccountOut]:
    items, next_cursor = account_service.list_accounts(db, params.limit, params.cursor)
    return Page(items=items, next_cursor=next_cursor)
