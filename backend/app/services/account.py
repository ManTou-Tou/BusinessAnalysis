"""Account 服务（用于多租户测试/管理）。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.schemas.account import AccountCreate
from app.utils.pagination import keyset_paginate


def create_account(db: Session, payload: AccountCreate) -> Account:
    account = Account(name=payload.name)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def list_accounts(
    db: Session, limit: int, cursor: int | None
) -> tuple[list[Account], int | None]:
    return keyset_paginate(db, select(Account), Account.id, limit, cursor)
