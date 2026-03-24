from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None


@dataclass(frozen=True)
class RateLimitRule:
    limit: int
    window_seconds: int


_LOCK = threading.Lock()
_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_REDIS_CLIENT = None


def reset_rate_limit_state() -> None:
    with _LOCK:
        _BUCKETS.clear()
    if _REDIS_CLIENT is not None:
        try:
            _REDIS_CLIENT.flushdb()
        except Exception:
            pass


def _get_redis_client():
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if not settings.REDIS_URL or redis is None:
        return None
    _REDIS_CLIENT = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _REDIS_CLIENT


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _enforce(rule_key: str, rule: RateLimitRule) -> None:
    now = time.time()
    threshold = now - rule.window_seconds
    client = _get_redis_client()
    if client is not None:
        member = f"{now:.9f}:{threading.get_ident()}"
        try:
            pipe = client.pipeline()
            pipe.zremrangebyscore(rule_key, 0, threshold)
            pipe.zadd(rule_key, {member: now})
            pipe.zcard(rule_key)
            pipe.expire(rule_key, rule.window_seconds)
            _, _, count, _ = pipe.execute()
            if int(count) > rule.limit:
                oldest = client.zrange(rule_key, 0, 0, withscores=True)
                retry_after = 1
                if oldest:
                    retry_after = int(max(rule.window_seconds - (now - float(oldest[0][1])), 1))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(retry_after)},
                )
                # no return
            return
        except HTTPException:
            raise
        except Exception:
            pass

    with _LOCK:
        q = _BUCKETS[rule_key]
        while q and q[0] <= threshold:
            q.popleft()
        if len(q) >= rule.limit:
            retry_after = int(max(rule.window_seconds - (now - q[0]), 1))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        q.append(now)


def rate_limit(scope: str, rules: tuple[RateLimitRule, ...]):
    def _dep(request: Request) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return
        ip = _client_ip(request)
        for rule in rules:
            key = f"{scope}:{ip}:{rule.limit}:{rule.window_seconds}"
            _enforce(key, rule)

    return Depends(_dep)
