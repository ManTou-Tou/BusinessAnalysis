"""分布式锁（Phase 8）：Redis SET NX PX + Lua 安全释放。

设计（见 PHASE_8_PLAN.md §1.4 / §5a#7-8）：
- 获取：`SET key token NX PX ttl`，token 每次唯一。
- 释放：Lua「比对 token 再 DEL」，防误删——锁过期后被他人续上时，不会把别人的锁删掉。
- 定位：锁是**减少重复执行的惊群**，不是正确性唯一来源。调用方（如 Beat 派发）本就
  幂等（idempotency_key + upsert），锁失效（TTL 到期 / Redis 故障）也不破坏正确性。
- 仅用现有 redis-py，不引 redlock 等库（CLAUDE.md 栈约束）。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from app.core.redis import redis_client

logger = logging.getLogger(__name__)

# 比对 token 再删，避免误删别人续上的锁。
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
else
  return 0
end
"""
_release_script = redis_client.register_script(_RELEASE_LUA)


@contextmanager
def redis_lock(key: str, ttl_s: int) -> Iterator[bool]:
    """尝试获取锁，yield 是否获取成功；退出时安全释放（仅释放自己持有的）。

    用法：
        with redis_lock("lock:foo", 600) as acquired:
            if not acquired:
                return  # 别处持锁，跳过
            ... 临界区 ...

    Redis 获取异常 → 视为未获取（fail-safe：宁可不跑也不并发跑；调用方幂等兜底）。
    """
    token = uuid4().hex
    acquired = False
    try:
        try:
            acquired = bool(redis_client.set(key, token, nx=True, px=ttl_s * 1000))
        except Exception:  # noqa: BLE001 - Redis 故障：视为未获取，调用方决定是否降级
            logger.warning("redis_lock acquire failed key=%s", key, exc_info=True)
            acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                _release_script(keys=[key], args=[token])
            except Exception:  # noqa: BLE001 - 释放失败靠 TTL 兜底过期
                logger.warning("redis_lock release failed key=%s", key, exc_info=True)
