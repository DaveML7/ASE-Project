"""Client for the db-manager: the only way a service reaches the database."""

import logging
import threading
import time

import requests

from .config import CATALOGUE_RETRY, CATALOGUE_TTL, SERVICE_URLS
from .resilience import CircuitOpen, guarded_request, pooled_session
from .service_identity import SERVICE_HEADER, service_token

log = logging.getLogger("ase.db")


class DbError(RuntimeError):
    """The db-manager rejected or failed a persistence operation."""

    def __init__(self, message: str, status_code: int | None = None, constraint: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.constraint = constraint

    def conflicts_with(self, constraint: str) -> bool:
        return self.status_code == 409 and self.constraint == constraint


class Catalogue:
    """The monster catalogue as a list and as an id index, cached for a short time.

    The index makes `by_id` O(1) instead of scanning the list on every request. Refreshing
    happens under a lock (one refresh, not one per thread) and, if the refresh fails, the
    last good copy keeps being served (stale-if-error): the catalogue changes rarely, so a
    database blip must not take the public catalogue down.
    """

    def __init__(self, load):
        self._load = load
        self._lock = threading.Lock()
        self._cached: tuple[float, list[dict], dict[int, dict]] | None = None

    def _fresh(self, now: float):
        cached = self._cached
        return cached if cached is not None and now - cached[0] <= CATALOGUE_TTL else None

    def _snapshot(self) -> tuple[list[dict], dict[int, dict]]:
        now = time.monotonic()
        fresh = self._fresh(now)
        if fresh is not None:
            return fresh[1], fresh[2]
        with self._lock:
            now = time.monotonic()
            fresh = self._fresh(now)
            if fresh is not None:
                return fresh[1], fresh[2]
            stale = self._cached
            try:
                rows = self._load()
            except (DbError, requests.RequestException, CircuitOpen):
                if stale is None:
                    raise
                log.warning("catalogue refresh failed, serving the cached copy")
                self._cached = (now - CATALOGUE_TTL + CATALOGUE_RETRY, stale[1], stale[2])
                return stale[1], stale[2]
            self._cached = (now, rows, {row["id"]: row for row in rows})
            return rows, self._cached[2]

    def all(self) -> list[dict]:
        return self._snapshot()[0]

    def by_id(self) -> dict[int, dict]:
        return self._snapshot()[1]


class DB:
    """CRUD client for the db-manager. Table/column names are checked there."""

    def __init__(self, base_url: str | None = None):
        self.base = (base_url or SERVICE_URLS["db-manager"]).rstrip("/")
        self.http = pooled_session()
        self._monsters = Catalogue(lambda: self.select("monsters", order_by="id", limit=500))

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
        """All monsters (cached)."""
        return self._monsters.all()

    def catalogue_by_id(self) -> dict[int, dict]:
        """The same catalogue indexed by id (cached, O(1) lookups)."""
        return self._monsters.by_id()

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

    def create_team(self, player_id: str, name: str, member_ids: list[int]) -> dict:
        """Create a team with its members atomically (limit enforced by the database)."""
        return self._call(
            "POST",
            "/db/teams/with-slots",
            json={"player_id": player_id, "name": name, "member_ids": member_ids},
        )

    def purge_expired_revocations(self) -> int:
        """Remove revoked-token entries that have expired (auth service only)."""
        return self._call("DELETE", "/db/revoked_tokens/expired")["deleted"]

    def batch(self, operations: list[dict]) -> list:
        """Run several operations in a single database transaction."""
        return self._call("POST", "/db/batch", json={"operations": operations})
