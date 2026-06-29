"""冒烟测试 seed：直接用 SQLAlchemy 写入基础数据（account + shops）。

这是「直接 seed」路径：不经任何 API/worker，直连本地 MySQL 落库。
products / orders / reviews 走 Excel 导入 API（见 make_xlsx.py + import_via_api.py）。

用法（在 backend/ 目录、已激活 venv、本地 MySQL 已迁移到 head）：
    python scripts/seed_base.py

幂等：account 按 name 唯一识别、shop 按 (account_id, external_shop_id) 唯一识别；
重复执行不会产生重复行。结束时打印 ACCOUNT_ID / SHOP_ID 供后续导入使用。
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.account import Account
from app.models.shop import Shop

ACCOUNT_NAME = "smoke-test"

# external_shop_id 是 Excel 导入 upsert 的稳定键；这里先建好，products 等再挂到对应 shop_id。
SHOPS = [
    {"platform": "shopee", "shop_name": "冒烟测试店-Shopee", "external_shop_id": "SMOKE-S1"},
    {"platform": "tiktok_shop", "shop_name": "冒烟测试店-TikTok", "external_shop_id": "SMOKE-S2"},
]


def get_or_create_account(db) -> Account:
    acc = db.execute(select(Account).where(Account.name == ACCOUNT_NAME)).scalar_one_or_none()
    if acc is None:
        acc = Account(name=ACCOUNT_NAME, status="active")
        db.add(acc)
        db.flush()  # 拿到 acc.id
    return acc


def get_or_create_shop(db, account_id: int, spec: dict) -> Shop:
    shop = db.execute(
        select(Shop).where(
            Shop.account_id == account_id,
            Shop.external_shop_id == spec["external_shop_id"],
        )
    ).scalar_one_or_none()
    if shop is None:
        shop = Shop(
            account_id=account_id,
            platform=spec["platform"],
            shop_name=spec["shop_name"],
            external_shop_id=spec["external_shop_id"],
            status="active",
        )
        db.add(shop)
        db.flush()
    return shop


def main() -> None:
    with SessionLocal() as db:
        acc = get_or_create_account(db)
        shops = [get_or_create_shop(db, acc.id, spec) for spec in SHOPS]
        db.commit()

        print("=" * 50)
        print(f"ACCOUNT_ID = {acc.id}   (name={acc.name!r})")
        for s in shops:
            print(f"SHOP_ID    = {s.id}   platform={s.platform:<12} external={s.external_shop_id}")
        print("=" * 50)
        print("下一步：python scripts/make_xlsx.py  然后  python scripts/import_via_api.py "
              f"--account-id {acc.id} --shop-id {shops[0].id}")


if __name__ == "__main__":
    main()
