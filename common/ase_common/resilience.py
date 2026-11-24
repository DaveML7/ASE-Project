"""Outgoing-call resilience: circuit breaker, bounded retry and one guarded request.

Every outgoing call goes through `guarded_request`:

* a short connect timeout and a separate read timeout (a dead host is detected in seconds);
* a small bounded retry for transient failures of idempotent calls (urllib3 `Retry`);
* a circuit breaker per target: after repeated failures calls fail fast with
  `CircuitOpen` (HTTP 503) instead of blocking a worker for the whole timeout, then a
  single trial call decides whether the target is back.
"""

import logging
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import BREAKER_FAILURES, BREAKER_RESET, CA_CERT, HTTP_TIMEOUT
from .request_context import context_headers

TRANSIENT_STATUS = (502, 503, 504)
log = logging.getLogger("ase.resilience")


class CircuitOpen(RuntimeError):
    """The target failed repeatedly: the call was refused without being attempted."""

    def __init__(self, target: str, retry_after: float):
        super().__init__(f"{target} is unavailable, retry in {retry_after:.0f}s")
        self.target = target
        self.retry_after = retry_after


class CircuitBreaker:
    """closed -> (N consecutive failures) -> open -> (after `reset_after`) -> half-open.

    In the half-open state exactly one trial call is let through; its outcome closes the
    circuit or opens it again. Thread-safe.
    """

    def __init__(self, target: str, failures: int, reset_after: float, clock=time.monotonic):
        self.target, self.failures, self.reset_after, self._clock = (
            target,
            failures,
            reset_after,
            clock,
        )
        self._lock = threading.Lock()
        self._count = 0
        self._opened_at: float | None = None
        self._trial_running = False

    def before_call(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            elapsed = self._clock() - self._opened_at
            if elapsed < self.reset_after or self._trial_running:
                raise CircuitOpen(self.target, max(self.reset_after - elapsed, 1.0))
            self._trial_running = True  # half-open: this call is the trial

    def success(self) -> None:
        with self._lock:
            self._count, self._opened_at, self._trial_running = 0, None, False

    def failure(self) -> None:
        with self._lock:
            self._count += 1
            self._trial_running = False
            if self._opened_at is not None or self._count >= self.failures:
                if self._opened_at is None:
                    log.warning("circuit opened for %s", self.target)
                self._opened_at = self._clock()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None


_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def breaker_for(target: str) -> CircuitBreaker:
    with _breakers_lock:
        if target not in _breakers:
            _breakers[target] = CircuitBreaker(target, BREAKER_FAILURES, BREAKER_RESET)
        return _breakers[target]


def pooled_session() -> requests.Session:
    """HTTPS session that verifies the project CA, keeps connections alive and retries.

    Reusing connections avoids a TCP + TLS handshake on every service-to-service call.
    Connection errors are retried for every method (nothing was sent); GET is also
    retried on read errors and 502/503/504.
    """
    session = requests.Session()
    session.verify = CA_CERT
    retry = Retry(
        total=2,
        connect=2,
        read=1,
        status=1,
        backoff_factor=0.1,
        allowed_methods=frozenset({"GET"}),
        status_forcelist=TRANSIENT_STATUS,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=retry))
    return session


def guarded_request(
    session: requests.Session, target: str, method: str, url: str, timeout=None, **kwargs
):
    """One outgoing call: circuit breaker, timeouts and failure accounting in one place."""
    breaker = breaker_for(target)
    breaker.before_call()
    headers = {**context_headers(), **kwargs.pop("headers", {})}
    try:
        response = session.request(
            method, url, headers=headers, timeout=timeout or HTTP_TIMEOUT, **kwargs
        )
    except Exception:
        # ANY failure (not only requests' own) must be accounted: otherwise a failed
        # half-open trial would leave the circuit open for ever.
        breaker.failure()
        raise
    if response.status_code in TRANSIENT_STATUS:
        breaker.failure()
    else:
        breaker.success()
    return response
