"""通过 Excel 导入 API 上传样例数据并轮询任务结果。

这是「走导入 API」路径：真正经过 Phase 9 链路（FastAPI 落盘建任务 → sync 队列 worker 解析入库）。
前置：API（uvicorn）、worker（celery -Q ...,sync）、Redis、MySQL 都已在运行；
且已先跑过 seed_base.py（拿 account_id/shop_id）与 make_xlsx.py（生成 xlsx）。

用法（在 backend/ 目录、已激活 venv）：
    python scripts/import_via_api.py --account-id 1 --shop-id 1
    # 可选：--base-url http://localhost:8000  --timeout 60
"""
from __future__ import annotations

import argparse
import os
import sys
import time

try:
    import httpx
except ImportError:
    sys.exit("缺少 httpx（应随 anthropic 一起装）。请在 venv 内 `pip install httpx` 后重试。")

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_data")
TERMINAL = {"succeeded", "failed", "cancelled"}

# (entity, 文件名, 额外表单字段) —— shops 这里不导（seed 已建），按需自行加。
JOBS = [
    ("products", "products.xlsx", {}),
    ("orders", "orders.xlsx", {}),
    ("reviews", "reviews.xlsx", {"conflict": "insert"}),  # reviews 仅 append-only
]


def upload(client: httpx.Client, base_url: str, account_id: int, shop_id: int,
           entity: str, filename: str, extra: dict) -> int | None:
    path = os.path.join(SAMPLE_DIR, filename)
    if not os.path.exists(path):
        print(f"[skip] {entity}: 找不到 {path}（先跑 make_xlsx.py）")
        return None
    data = {"shop_id": str(shop_id), **extra}  # products/orders/reviews 都需 shop_id
    with open(path, "rb") as fh:
        files = {"file": (filename, fh,
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        resp = client.post(
            f"{base_url}/api/v1/imports/{entity}",
            headers={"X-Account-Id": str(account_id)},
            data=data,
            files=files,
        )
    if resp.status_code != 202:
        print(f"[fail] {entity}: HTTP {resp.status_code} {resp.text}")
        return None
    task = resp.json()
    print(f"[ok]   {entity}: 任务 id={task['id']} status={task['status']}")
    return task["id"]


def poll(client: httpx.Client, base_url: str, account_id: int, task_id: int,
         timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while True:
        resp = client.get(
            f"{base_url}/api/v1/agent/tasks/{task_id}",
            headers={"X-Account-Id": str(account_id)},
        )
        resp.raise_for_status()
        task = resp.json()
        status = task["status"]
        if status in TERMINAL:
            out = task.get("output_json") or {}
            print(f"  task {task_id} -> {status}")
            print(f"    inserted={out.get('inserted')} updated={out.get('updated')} "
                  f"error_count={out.get('error_count')} processed={out.get('processed_rows')}")
            for err in (out.get("errors") or [])[:10]:
                print(f"      error: {err}")
            if status == "failed":
                print(f"    error_type={task.get('error_type')} msg={task.get('error_message')}")
            return
        if time.monotonic() > deadline:
            print(f"  task {task_id} 仍为 {status}（超时 {timeout}s）。"
                  f"检查 worker 是否在跑且监听 sync 队列。")
            return
        time.sleep(1.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account-id", type=int, required=True)
    ap.add_argument("--shop-id", type=int, required=True)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    base_url = args.base_url.rstrip("/")
    with httpx.Client(timeout=30) as client:
        for entity, filename, extra in JOBS:
            task_id = upload(client, base_url, args.account_id, args.shop_id,
                             entity, filename, extra)
            if task_id is not None:
                poll(client, base_url, args.account_id, task_id, args.timeout)


if __name__ == "__main__":
    main()
