"""Clients for the db-manager microservice and for peer services."""

import os
import time

import requests
from requests.adapters import HTTPAdapter

from .auth import SERVICE_HEADER, service_token

CA_CERT = os.getenv("CA_CERT", "/run/app-config/ca.crt")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
CATALOGUE_TTL = float(os.getenv("CATALOGUE_TTL_SECONDS", "30"))

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


def pooled_session() -> requests.Session:
    """HTTPS session that verifies the project CA and keeps connections alive.

    Reusing connections avoids a TCP + TLS handshake on every service-to-service call.
    """
    session = requests.Session()
    session.verify = CA_CERT
    adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32)
    session.mount("https://", adapter)
    return session


class DB:
    """CRUD client for the db-manager. Table/column names are checked there."""

    def __init__(self, base_url: str | None = None):
        self.base = (base_url or SERVICE_URLS["db-manager"]).rstrip("/")
        self.http = pooled_session()
        self._catalogue: tuple[float, list[dict]] | None = None

    def _call(self, method, path, allow_404=False, **kwargs):
        response = self.http.request(
            method,
            f"{self.base}{path}",
            timeout=HTTP_TIMEOUT,
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
                status_code=response.status_code, constraint=constraint,
            )
        return response.json() if response.content else None

    def insert(self, table: str, values: dict) -> dict:
        return self._call("POST", f"/db/{table}", json=values)

    @staticmethod
    def _filters(filters: dict) -> dict:
        """`col=value` -> equality; `col__in=[...]` -> membership (one query, no N+1)."""
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

    def count(self, table: str, **filters) -> int:
        return self._call("GET", f"/db/{table}/count", params=self._filters(filters))["count"]

    def catalogue(self) -> list[dict]:
        """All monsters, cached for a short time: the catalogue is read on almost every request."""
        now = time.monotonic()
        if self._catalogue is None or now - self._catalogue[0] > CATALOGUE_TTL:
            self._catalogue = (now, self.select("monsters", order_by="id", limit=500))
        return self._catalogue[1]

    def get(self, table: str, row_id):
        return self._call("GET", f"/db/{table}/{row_id}", allow_404=True)

    def update(self, table: str, row_id, values: dict) -> dict:
        return self._call("PATCH", f"/db/{table}/{row_id}", json=values)

    def delete(self, table: str, row_id) -> None:
        self._call("DELETE", f"/db/{table}/{row_id}")

    def replace_team_slots(self, team_id: int, member_ids: list[int], name: str | None = None) -> dict:
        return self._call("PUT", f"/db/teams/{team_id}/slots", json={"member_ids": member_ids, "name": name})

    def batch(self, operations: list[dict]) -> list:
        """Run several operations in a single database transaction."""
        return self._call("POST", "/db/batch", json={"operations": operations})


_peer_session: requests.Session | None = None


def peer_session() -> requests.Session:
    global _peer_session
    if _peer_session is None:
        _peer_session = pooled_session()
    return _peer_session


def call_service(
    service: str, method: str, path: str, user_token: str | None = None, **kwargs
):
    """Call a peer service over HTTPS, signed as this service.

    `user_token` forwards the end user's own JWT: the callee verifies it itself, so a
    service can only act on behalf of a player whose token it actually holds.
    """
    merged = {SERVICE_HEADER: service_token(service)}
    if user_token:
        merged["Authorization"] = f"Bearer {user_token}"
    return peer_session().request(
        method,
        f"{SERVICE_URLS[service].rstrip('/')}{path}",
        headers=merged,
        timeout=HTTP_TIMEOUT,
        **kwargs,
    )
