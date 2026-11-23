"""Logged-out tokens: the gateway refuses them at once.

The list lives in the auth service's database. The gateway refreshes its copy every few
seconds and adds a token to its own set the moment a logout passes through, so a logout is
effective immediately and survives a restart (the list is reloaded from the database).
"""

import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger("gateway.revocation")


class RevocationUnavailable(RuntimeError):
    """No revocation list has been loaded yet, so no token can be trusted."""


class RevocationList:
    def __init__(
        self,
        fetch: Callable[[], list[str]],
        refresh_seconds: float = 5.0,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self._fetch, self._every = fetch, refresh_seconds
        self._clock, self._wall = clock, wall_clock
        self._remote: frozenset[str] | None = None
        self._local: dict[str, int] = {}  # jti -> expiry, revoked here but maybe not yet remote
        self._synced = float("-inf")
        self._refreshing = threading.Lock()
        self._state = threading.Lock()

    def is_revoked(self, jti: str) -> bool:
        self._refresh_if_due()
        remote = self._remote
        if remote is None:
            raise RevocationUnavailable("revocation state unavailable")
        with self._state:
            return jti in remote or jti in self._local

    def revoke_locally(self, jti: str, expires_at: int) -> None:
        with self._state:
            self._local[jti] = expires_at

    def mark_stale(self) -> None:
        """Make the next check reload the list (used after a restart-like event and in tests)."""
        self._synced = float("-inf")

    def _refresh_if_due(self) -> None:
        """Reload at most every few seconds, in ONE thread; the others keep using the old
        list. If the auth service is unreachable the previous list is kept."""
        if self._clock() - self._synced < self._every:
            return
        if not self._refreshing.acquire(blocking=False):
            return
        try:
            if self._clock() - self._synced < self._every:
                return
            self._synced = self._clock()  # even on failure: do not hammer a failing service
            with self._state:
                now = self._wall()
                self._local = {j: e for j, e in self._local.items() if e > now}
            try:
                self._remote = frozenset(self._fetch())
            except Exception as exc:  # noqa: BLE001 - any failure keeps the previous list
                log.warning("revocation list unavailable: %s", exc)
        finally:
            self._refreshing.release()
