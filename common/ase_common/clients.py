"""Clients for the db-manager and for peer services, with the resilience they need.

Every outgoing call goes through `guarded_request`:

* a short connect timeout and a separate read timeout (a dead host is detected in seconds);
* a small bounded retry for transient failures of idempotent calls (urllib3 `Retry`);
* a circuit breaker per target: after repeated failures calls fail fast with
  `CircuitOpen` (HTTP 503) instead of blocking a worker for the whole timeout, then a
  single trial call decides whether the target is back.
"""

import logging
import os
import threading
import time

import requests
from flask import g, has_request_context
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .auth import SERVICE_HEADER, service_token

CA_CERT = os.getenv("CA_CERT", "/run/app-config/ca.crt")
CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "2"))
READ_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
HTTP_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
CATALOGUE_TTL = float(os.getenv("CATALOGUE_TTL_SECONDS", "30"))
CATALOGUE_RETRY = 5.0  # seconds before trying again after a failed refresh
BREAKER_FAILURES = int(os.getenv("BREAKER_FAILURES", "5"))
BREAKER_RESET = float(os.getenv("BREAKER_RESET_SECONDS", "10"))
REQUEST_ID_HEADER = "X-Request-ID"
TRANSIENT_STATUS = (502, 503, 504)

log = logging.getLogger("ase.clients")

SERVICE_URLS = {
    "auth": os.getenv("AUTH_URL", "https://auth:5001"),
    "players": os.getenv("PLAYERS_URL", "https://players:5002"),
    "monsters": os.getenv("MONSTERS_URL", "https://monsters:5003"),
    "teams": os.getenv("TEAMS_URL", "https://teams:5004"),
    "encounters": os.getenv("ENCOUNTERS_URL", "https://encounters:5005"),
    "history": os.getenv("HISTORY_URL", "https://history:5006"),
    "db-manager": os.getenv("DB_MANAGER_URL", "https://db-manager:5010"),
}


class DbError(RuntimeError):
    """The db-manager rejected or failed a persistence operation."""

    def __init__(self, message: str, status_code: int | None = None, constraint: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.constraint = constraint

    def conflicts_with(self, constraint: str) -> bool:
        return self.status_code == 409 and self.constraint == constraint


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


def _context_headers() -> dict:
    """Headers that follow a user request through every service it touches."""
    if has_request_context() and getattr(g, "request_id", None):
        return {REQUEST_ID_HEADER: g.request_id}
    return {}


def guarded_request(
    session: requests.Session, target: str, method: str, url: str, timeout=None, **kwargs
):
    """One outgoing call: circuit breaker, timeouts and failure accounting in one place."""
    breaker = breaker_for(target)
    breaker.before_call()
    headers = {**_context_headers(), **kwargs.pop("headers", {})}
    try:
        response = session.request(
            method, url, headers=headers, timeout=timeout or HTTP_TIMEOUT, **kwargs
        )
    except requests.RequestException:
        breaker.failure()
        raise
    if response.status_code in TRANSIENT_STATUS:
        breaker.failure()
    else:
        breaker.success()
    return response


class DB:
    """CRUD client for the db-manager. Table/column names are checked there."""

    def __init__(self, base_url: str | None = None):
        self.base = (base_url or SERVICE_URLS["db-manager"]).rstrip("/")
        self.http = pooled_session()
        self._catalogue: tuple[float, list[dict]] | None = None
        self._catalogue_lock = threading.Lock()

    def _call(self, method, path, allow_404=False, **kwargs):
        response = guarded_request(
            self.http,
            "db-manager",
            method,
            f"{self.base}{path}",
            headers={SERVICE_HEADER: service_token("db-manager")},
            **kwargs,
        )
        if response.status_code == 404 and allow_404:
            return None
        if response.status_code >= 400:
            constraint = None
            if response.status_code == 409:
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        constraint = body.get("constraint")
                except ValueError:
                    pass
            raise DbError(
                f"{method} {path} -> {response.status_code}",
                status_code=response.status_code,
                constraint=constraint,
            )
        return response.json() if response.content else None

    def insert(self, table: str, values: dict) -> dict:
        return self._call("POST", f"/db/{table}", json=values)

    @staticmethod
    def _filters(filters: dict) -> dict:
        """`col=value` -> equality; `col__gt=v` -> greater than; `col__in=[...]` -> membership."""
        params = {}
        for column, value in filters.items():
            if column.endswith("__gt"):
                params[f"gt.{column.removesuffix('__gt')}"] = value
            elif column.endswith("__in"):
                params[f"in.{column.removesuffix('__in')}"] = ",".join(str(item) for item in value)
            else:
                params[f"eq.{column}"] = value
        return params

    def select(self, table: str, order_by: str | None = None, limit: int = 200, **filters):
        params = self._filters(filters)
        params["limit"] = limit
        if order_by:
            params["order_by"] = order_by
        return self._call("GET", f"/db/{table}", params=params)

    def encounter_history(self, player_id: str, limit: int, before: int | None) -> list[dict]:
        return self._call(
            "GET",
            "/db/encounters/history",
            params={"player_id": player_id, "limit": limit, "before": before},
        )

    def count(self, table: str, **filters) -> int:
        return self._call("GET", f"/db/{table}/count", params=self._filters(filters))["count"]

    def catalogue(self) -> list[dict]:
        """All monsters, cached for a short time: the catalogue is read on almost every request.

        Refreshing happens under a lock (one refresh, not one per thread) and, if the refresh
        fails, the last good copy keeps being served (stale-if-error): the catalogue changes
        rarely, so a database blip must not take the public catalogue down.
        """
        cached = self._catalogue
        if cached is not None and time.monotonic() - cached[0] <= CATALOGUE_TTL:
            return cached[1]
        with self._catalogue_lock:
            cached = self._catalogue
            now = time.monotonic()
            if cached is not None and now - cached[0] <= CATALOGUE_TTL:
                return cached[1]
            try:
                rows = self.select("monsters", order_by="id", limit=500)
            except (DbError, requests.RequestException, CircuitOpen):
                if cached is None:
                    raise
                log.warning("catalogue refresh failed, serving the cached copy")
                self._catalogue = (now - CATALOGUE_TTL + CATALOGUE_RETRY, cached[1])
                return cached[1]
            self._catalogue = (now, rows)
            return rows

    def get(self, table: str, row_id):
        return self._call("GET", f"/db/{table}/{row_id}", allow_404=True)

    def update(self, table: str, row_id, values: dict) -> dict:
        return self._call("PATCH", f"/db/{table}/{row_id}", json=values)

    def delete(self, table: str, row_id) -> None:
        self._call("DELETE", f"/db/{table}/{row_id}")

    def replace_team_slots(
        self, team_id: int, member_ids: list[int], name: str | None = None
    ) -> dict:
        return self._call(
            "PUT", f"/db/teams/{team_id}/slots", json={"member_ids": member_ids, "name": name}
        )

    def batch(self, operations: list[dict]) -> list:
        """Run several operations in a single database transaction."""
        return self._call("POST", "/db/batch", json={"operations": operations})


_peer_session: requests.Session | None = None
_peer_lock = threading.Lock()


def peer_session() -> requests.Session:
    global _peer_session
    with _peer_lock:
        if _peer_session is None:
            _peer_session = pooled_session()
        return _peer_session


def call_service(
    service: str,
    method: str,
    path: str,
    user_token: str | None = None,
    timeout=None,
    **kwargs,
):
    """Call a peer service over HTTPS, signed as this service.

    `user_token` forwards the end user's own JWT: the callee verifies it itself, so a
    service can only act on behalf of a player whose token it actually holds.
    """
    headers = {SERVICE_HEADER: service_token(service)}
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
    return guarded_request(
        peer_session(),
        service,
        method,
        f"{SERVICE_URLS[service].rstrip('/')}{path}",
        timeout=timeout,
        headers=headers,
        **kwargs,
    )
