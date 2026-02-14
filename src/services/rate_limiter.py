# -*- coding: utf-8 -*-
"""
===================================
Rate limiter for image extraction
===================================

In-memory, thread-safe rate limiting for extract-from-image API.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded. Use detail dict for HTTP 429."""

    def __init__(self, message: str, window_seconds: int = 60):
        super().__init__(message)
        self.message = message
        self.window_seconds = window_seconds


class MemoryRateLimiter:
    """In-memory rate limiter. Thread-safe."""

    def __init__(self) -> None:
        self._times: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check_rate_limit(self, key: str, limit: int, window: int) -> None:
        """Raise RateLimitExceeded if limit exceeded. key is typically client IP."""
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


_extract_limiter: MemoryRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_extract_rate_limiter() -> MemoryRateLimiter:
    """Return rate limiter for extract-from-image."""
    global _extract_limiter
    if _extract_limiter is not None:
        return _extract_limiter
    with _limiter_lock:
        if _extract_limiter is not None:
            return _extract_limiter
        _extract_limiter = MemoryRateLimiter()
        return _extract_limiter
