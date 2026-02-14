# -*- coding: utf-8 -*-
"""
===================================
Rate limiter for image extraction
===================================

Provides in-memory (single instance) or Redis-backed (distributed) rate limiting.
Use REDIS_URL env to enable distributed limit when running multiple instances.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded. Use detail dict for HTTP 429."""

    def __init__(self, message: str, window_seconds: int = 60):
        super().__init__(message)
        self.message = message
        self.window_seconds = window_seconds


class BaseRateLimiter(ABC):
    """Abstract rate limiter."""

    @abstractmethod
    def check_rate_limit(self, key: str, limit: int, window: int) -> None:
        """Raise RateLimitExceeded if limit exceeded. key is typically client IP."""
        pass


class MemoryRateLimiter(BaseRateLimiter):
    """In-memory rate limiter (single instance). Thread-safe."""

    def __init__(self) -> None:
        self._times: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check_rate_limit(self, key: str, limit: int, window: int) -> None:
        now = time.time()
        cutoff = now - window
        with self._lock:
            times = self._times[key]
            times[:] = [t for t in times if t > cutoff]
            if len(times) >= limit:
                raise RateLimitExceeded(
                    f"图片识别请求过于频繁，请 {window} 秒后再试", window_seconds=window
                )
            times.append(now)


class RedisRateLimiter(BaseRateLimiter):
    """Redis-backed rate limiter (distributed). Sliding window via sorted set."""

    def __init__(self, redis_url: str, key_prefix: str = "extract:") -> None:
        try:
            import redis
        except ImportError:
            raise ImportError("redis package required for REDIS_URL. Run: pip install redis")
        self._client = redis.from_url(redis_url, decode_responses=True)
        self._key_prefix = key_prefix

    def check_rate_limit(self, key: str, limit: int, window: int) -> None:
        rkey = f"{self._key_prefix}{key}"
        now = time.time()
        window_start = now - window
        try:
            pipe = self._client.pipeline()
            pipe.zremrangebyscore(rkey, 0, window_start)
            pipe.zadd(rkey, {str(now): now})
            pipe.expire(rkey, window + 1)
            pipe.zcard(rkey)
            results = pipe.execute()
            count = results[3]
            if count > limit:
                self._client.zrem(rkey, str(now))
                raise RateLimitExceeded(
                    f"图片识别请求过于频繁，请 {window} 秒后再试", window_seconds=window
                )
        except RateLimitExceeded:
            raise
        except Exception as e:
            logger.warning(f"Redis rate limit check failed: {e}, allowing request")


_extract_limiter: Optional[BaseRateLimiter] = None
_limiter_lock = threading.Lock()


def get_extract_rate_limiter() -> BaseRateLimiter:
    """Return rate limiter for extract-from-image. Redis if REDIS_URL set, else memory."""
    global _extract_limiter
    if _extract_limiter is not None:
        return _extract_limiter
    with _limiter_lock:
        if _extract_limiter is not None:
            return _extract_limiter
        redis_url = os.getenv("REDIS_URL", "").strip()
        if redis_url:
            try:
                _extract_limiter = RedisRateLimiter(redis_url)
                logger.info("Using Redis-backed rate limiter for image extraction")
            except Exception as e:
                logger.warning(f"Redis rate limiter init failed: {e}, falling back to memory")
                _extract_limiter = MemoryRateLimiter()
        else:
            _extract_limiter = MemoryRateLimiter()
            logger.debug("Using in-memory rate limiter for image extraction")
        return _extract_limiter
