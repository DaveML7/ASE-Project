"""Sliding-window rate limiter (in memory, no dependency).

Counters live in process memory and are protected by a lock, so they are exact
as long as the service runs as ONE process with several threads (the gateway is
deployed that way). A shared store (Redis) would be needed to scale it to several
processes or hosts.

Memory is bounded twice: expired keys are swept about once per window (so the table
holds only clients active in the last minute) and a hard cap protects against floods.
"""

import threading
import time
from collections import deque

MAX_KEYS = 50_000  # hard cap: an attacker cannot grow the table forever


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._last_sweep = clock()

    def check(self, key: str, limit: int, window: float = 60.0) -> float:
        """Record a hit. Return 0 if allowed, else the seconds until a slot frees up."""
        now = self._clock()
        with self._lock:
            if now - self._last_sweep >= window or len(self._hits) >= MAX_KEYS:
                self._sweep(now, window)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= limit:
                return max(window - (now - hits[0]), 0.001)
            hits.append(now)
            return 0.0

    def _sweep(self, now: float, window: float) -> None:
        self._last_sweep = now
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= window]:
            del self._hits[key]
        if len(self._hits) >= MAX_KEYS:  # still full of live keys: drop the oldest half
            for key in list(self._hits)[: MAX_KEYS // 2]:
                del self._hits[key]

    def __len__(self) -> int:
        return len(self._hits)
