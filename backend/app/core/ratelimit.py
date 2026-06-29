"""API 限流（Phase 8）：Redis 固定窗口 + Lua 原子计数，fail-open。

设计（见 PHASE_8_PLAN.md §1.1 / §5a#1-5）：
- 维度 = 路由 + 租户（X-Account-Id），拿不到租户退化到客户端 IP。
- 固定窗口：`INCR` + 首次 `PEXPIRE`，合成单条 Lua 保证原子——杜绝「计数留存但
  过期丢失 → 配额永久锁死」。窗口自首请求起算（边界突发是已知取舍，MVP 接受）。
- **fail-open**：Redis 任何异常 → 记日志 + 放行；限流是保护层，不该自己挂了把正常请求拒掉。
- 不引第三方限流库（slowapi 等），仅用现有 redis-py（CLAUDE.md 栈约束）。
"""
from __future__ import annotations

import logging
from math import ceil

from fastapi import Header, HTTPException, Request, status

from app.core.config import settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

# INCR + 首次 PEXPIRE + 取 PTTL，单条原子脚本。返回 {当前计数, 剩余毫秒}。
_FIXED_WINDOW_LUA = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('PTTL', KEYS[1])
return {c, ttl}
"""
_script = redis_client.register_script(_FIXED_WINDOW_LUA)


def _hit(key: str, window_ms: int) -> tuple[int, int]:
    """对 key 计数一次，返回 (当前计数, 剩余毫秒)。Redis 异常向上抛由调用方 fail-open。"""
    count, ttl_ms = _script(keys=[key], args=[window_ms])
    return int(count), int(ttl_ms)


def RateLimit(route_id: str, limit: int | None = None, window_s: int | None = None):
    """构造一个 FastAPI 依赖：对 (route_id, 租户/IP) 在固定窗口内限流。

    命中上限抛 429 + Retry-After。route_id 用稳定常量（非原始 path），避免路径
    参数撑爆 key 空间。limit/window_s 省略时用全局默认。
    """

    def dependency(
        request: Request,
        x_account_id: str | None = Header(default=None, alias="X-Account-Id"),
    ) -> None:
        if not settings.ratelimit_enabled:
            return

        lim = limit if limit is not None else settings.ratelimit_default_limit
        win = window_s if window_s is not None else settings.ratelimit_default_window_s

        # 维度：优先租户，但**把 X-Account-Id 规范化为整数**（合法账号即 int），
        # 防止恶意/超长 header 直接进 key 撑爆 Redis（§5a#5）。无法解析为 int 时
        # 退化到客户端 IP，兜底 'unknown'。
        subject: str | None = None
        if x_account_id:
            try:
                subject = f"acct:{int(x_account_id)}"
            except (TypeError, ValueError):
                subject = None
        if subject is None:
            client = request.client
            subject = f"ip:{client.host if client else 'unknown'}"
        key = f"rl:{route_id}:{subject}"

        try:
            count, ttl_ms = _hit(key, win * 1000)
        except Exception:  # noqa: BLE001 - fail-open：Redis 故障不拦正常请求
            logger.warning("rate limit check failed (fail-open) key=%s", key, exc_info=True)
            return

        if count > lim:
            retry_after = max(1, ceil(ttl_ms / 1000)) if ttl_ms > 0 else win
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "rate_limited",
                    "scope": route_id,
                    "limit": lim,
                    "window_s": win,
                    "retry_after_s": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

    return dependency
