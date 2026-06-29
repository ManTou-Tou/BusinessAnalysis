"""限流依赖单测（Phase 8）：mock 计数，不依赖真实 Redis。

验证：放行/拦截(429+Retry-After)/fail-open/开关旁路/维度键。
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core import ratelimit
from app.core.config import settings


def _req(host: str = "1.2.3.4"):
    return SimpleNamespace(client=SimpleNamespace(host=host))


def test_allows_under_limit(monkeypatch):
    monkeypatch.setattr(ratelimit, "_hit", lambda key, win_ms: (1, 1000))
    dep = ratelimit.RateLimit("t.route", limit=60, window_s=60)
    # 不抛即放行
    assert dep(_req(), x_account_id="7") is None


def test_blocks_over_limit_with_retry_after(monkeypatch):
    monkeypatch.setattr(ratelimit, "_hit", lambda key, win_ms: (61, 5000))
    dep = ratelimit.RateLimit("t.route", limit=60, window_s=60)
    with pytest.raises(HTTPException) as ei:
        dep(_req(), x_account_id="7")
    exc = ei.value
    assert exc.status_code == 429
    assert exc.headers["Retry-After"] == "5"  # ceil(5000ms/1000)
    assert exc.detail["scope"] == "t.route"
    assert exc.detail["limit"] == 60


def test_fail_open_on_redis_error(monkeypatch):
    def boom(key, win_ms):
        raise RuntimeError("redis down")

    monkeypatch.setattr(ratelimit, "_hit", boom)
    dep = ratelimit.RateLimit("t.route", limit=1, window_s=60)
    # Redis 故障 → 放行（不抛）
    assert dep(_req(), x_account_id="7") is None


def test_disabled_bypasses(monkeypatch):
    called = {"n": 0}

    def counting(key, win_ms):
        called["n"] += 1
        return (999, 1000)

    monkeypatch.setattr(ratelimit, "_hit", counting)
    monkeypatch.setattr(settings, "ratelimit_enabled", False)
    dep = ratelimit.RateLimit("t.route", limit=1, window_s=60)
    assert dep(_req(), x_account_id="7") is None
    assert called["n"] == 0  # 关闭时根本不计数


def test_key_dimension_account_vs_ip(monkeypatch):
    seen = {}

    def capture(key, win_ms):
        seen["key"] = key
        return (1, 1000)

    monkeypatch.setattr(ratelimit, "_hit", capture)
    monkeypatch.setattr(settings, "ratelimit_enabled", True)
    dep = ratelimit.RateLimit("t.route", limit=60, window_s=60)

    dep(_req(), x_account_id="7")
    assert seen["key"] == "rl:t.route:acct:7"

    dep(_req(host="9.9.9.9"), x_account_id=None)
    assert seen["key"] == "rl:t.route:ip:9.9.9.9"
