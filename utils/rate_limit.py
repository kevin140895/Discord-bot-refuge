import asyncio  # required for asynchronous rate limiting
import logging
import os
import time
from typing import Dict
from collections import Counter

from utils.metrics import errors


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float) -> None:
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self.tokens = float(capacity)
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        delta = now - self.updated
        self.updated = now
        if delta > 0:
            self.tokens = min(self.capacity, self.tokens + delta * self.refill_rate)

    async def acquire(self, n: int = 1) -> None:
        n = float(n)
        async with self.lock:
            while True:
                self._refill()
                if self.tokens >= n:
                    self.tokens -= n
                    return
                needed = n - self.tokens
                wait = needed / self.refill_rate if self.refill_rate > 0 else 0.0
                await asyncio.sleep(wait)


class GlobalRateLimiter:
    """Simple token bucket based global rate limiter."""

    def __init__(self) -> None:
        self.strict = os.getenv("RATE_LIMIT_STRICT", "true").lower() == "true"
        self.global_rps = int(os.getenv("GLOBAL_RPS", "50"))
        self.buckets: Dict[str, TokenBucket] = {}
        self.logger = logging.getLogger("rate_limit")
        self._task: asyncio.Task | None = None
        self._requests = 0
        self._total_wait = 0.0
        self.errors: Counter[str] = errors

    def _get_bucket(self, name: str) -> TokenBucket:
        bucket = self.buckets.get(name)
        if bucket is None:
            if name == "global":
                bucket = TokenBucket(self.global_rps, self.global_rps)
            elif name.startswith("channel:"):
                bucket = TokenBucket(5, 1)  # 5 messages / 5s per channel
            elif name == "reactions":
                bucket = TokenBucket(4, 4)  # 4 reactions per second globally
            elif name.startswith("roles:"):
                bucket = TokenBucket(10, 1)  # 10 roles / 10s per member
            elif name == "channel_edit":
                bucket = TokenBucket(1, 1)  # 1 channel edit per second globally
            elif name.startswith("channel_edit:"):
                bucket = TokenBucket(2, 1 / 300)  # 2 edits / 10 min per channel
            else:
                bucket = TokenBucket(self.global_rps, self.global_rps)
            self.buckets[name] = bucket
        return bucket

    async def acquire(self, n: int = 1, bucket: str = "global") -> None:
        if not self.strict:
            self._requests += n
            return
        bucket_obj = self._get_bucket(bucket)
        start = time.monotonic()
        await bucket_obj.acquire(n)
        elapsed = time.monotonic() - start
        self._requests += n
        self._total_wait += elapsed
        if elapsed > 0.1:
            self.logger.debug("Rate limiter waited %.3fs for bucket %s", elapsed, bucket)

    def start(self) -> None:
        if self.strict and self._task is None:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._log_loop())

    async def _log_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            if self._requests:
                avg = self._total_wait / self._requests
            else:
                avg = 0.0
            self.logger.info(
                "Limiter processed %d requests, avg wait %.3fs", self._requests, avg
            )
            if self.errors:
                self.logger.info("Errors: %s", dict(self.errors))
                self.errors.clear()
            self._requests = 0
            self._total_wait = 0.0


# shared global rate limiter instance
limiter = GlobalRateLimiter()


__all__ = ["GlobalRateLimiter", "limiter"]
