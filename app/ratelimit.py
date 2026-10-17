"""Tiny token-bucket limiter so one noisy phone can't flood the server."""
from __future__ import annotations

import time


class TokenBucket:
    """Allows `burst` messages at once, refilling at `rate` messages per second."""

    def __init__(self, rate: float = 5.0, burst: int = 20) -> None:
        self.rate = rate
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.updated = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
