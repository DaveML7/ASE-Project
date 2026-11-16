"""Sliding-window rate limiter (in memory, no dependency).

Counters live in process memory and are protected by a lock, so they are exact
as long as the service runs as ONE process with several threads (the gateway is
deployed that way). A shared store (Redis) would be needed to scale it to several
processes or hosts.
"""

import threading
import time
from collections import deque

MAX_KEYS = 50_000  # bound memory: an attacker cannot grow the table forever


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def check(self, key: str, limit: int, window: float = 60.0) -> float:
        """Record a hit. Return 0 if allowed, else the seconds until a slot frees up."""
        now = self._clock()
        with self._lock:
            if len(self._hits) >= MAX_KEYS:
                self._purge(now, window)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= limit:
                return max(window - (now - hits[0]), 0.001)
            hits.append(now)
            return 0.0

    def _purge(self, now: float, window: float) -> None:
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= window]:
            del self._hits[key]
        if len(self._hits) >= MAX_KEYS:  # still full of live keys: drop the oldest half
            for key in list(self._hits)[: MAX_KEYS // 2]:
                del self._hits[key]
