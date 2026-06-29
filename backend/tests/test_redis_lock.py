"""分布式锁单测（Phase 8）：mock redis_client，不依赖真实 Redis。

验证：NX 获取/未获取、安全释放（比对 token 再删）、获取异常 fail-safe。
"""
from app.core import lock


def test_acquire_and_safe_release(monkeypatch):
    calls = {}

    def fake_set(key, value, nx=False, px=None):
        calls["set"] = {"key": key, "value": value, "nx": nx, "px": px}
        return True  # 获取成功

    def fake_release(keys, args):
        calls["release"] = {"keys": keys, "args": args}
        return 1

    monkeypatch.setattr(lock.redis_client, "set", fake_set)
    monkeypatch.setattr(lock, "_release_script", fake_release)

    with lock.redis_lock("lock:x", 600) as acquired:
        assert acquired is True
        token = calls["set"]["value"]
        assert calls["set"]["nx"] is True
        assert calls["set"]["px"] == 600 * 1000

    # 退出时用同一 token 释放（比对 token 再删）
    assert calls["release"]["keys"] == ["lock:x"]
    assert calls["release"]["args"] == [token]


def test_not_acquired_skips_release(monkeypatch):
    calls = {"release": False}
    monkeypatch.setattr(lock.redis_client, "set", lambda *a, **k: None)  # 未获取
    monkeypatch.setattr(
        lock, "_release_script", lambda keys, args: calls.__setitem__("release", True)
    )

    with lock.redis_lock("lock:x", 600) as acquired:
        assert acquired is False

    assert calls["release"] is False  # 未持锁不得释放（防误删他人锁）


def test_acquire_exception_fail_safe(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(lock.redis_client, "set", boom)
    monkeypatch.setattr(
        lock, "_release_script", lambda keys, args: (_ for _ in ()).throw(AssertionError)
    )

    with lock.redis_lock("lock:x", 600) as acquired:
        assert acquired is False  # 获取异常视为未获取，调用方据此降级
