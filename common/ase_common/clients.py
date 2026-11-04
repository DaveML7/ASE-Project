"""Clients for the db-manager microservice and for peer services."""

import os

import requests

CA_CERT = os.getenv("CA_CERT", "/certs/ca.crt")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

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


class DB:
    """CRUD client for the db-manager. Table/column names are checked there."""

    def __init__(self, base_url: str | None = None):
        self.base = (base_url or SERVICE_URLS["db-manager"]).rstrip("/")
        self.http = requests.Session()
        self.http.verify = CA_CERT
        self.http.headers["X-Internal-Key"] = INTERNAL_API_KEY

    def _call(self, method, path, allow_404=False, **kwargs):
        response = self.http.request(
            method, f"{self.base}{path}", timeout=HTTP_TIMEOUT, **kwargs
        )
        if response.status_code == 404 and allow_404:
            return None
        if response.status_code >= 400:
            raise DbError(f"{method} {path} -> {response.status_code} {response.text[:200]}")
        return response.json() if response.content else None

    def insert(self, table: str, values: dict) -> dict:
        return self._call("POST", f"/db/{table}", json=values)

    def select(self, table: str, order_by: str | None = None, limit: int = 200, **filters):
        params = {f"eq.{column}": value for column, value in filters.items()}
        params["limit"] = limit
        if order_by:
            params["order_by"] = order_by
        return self._call("GET", f"/db/{table}", params=params)

    def get(self, table: str, row_id):
        return self._call("GET", f"/db/{table}/{row_id}", allow_404=True)

    def update(self, table: str, row_id, values: dict) -> dict:
        return self._call("PATCH", f"/db/{table}/{row_id}", json=values)

    def delete(self, table: str, row_id) -> None:
        self._call("DELETE", f"/db/{table}/{row_id}")

    def batch(self, operations: list[dict]) -> list:
        """Run several operations in a single database transaction."""
        return self._call("POST", "/db/batch", json={"operations": operations})


def call_service(service: str, method: str, path: str, headers: dict | None = None, **kwargs):
    """Call a peer microservice over HTTPS with the internal key attached."""
    merged = {"X-Internal-Key": INTERNAL_API_KEY}
    merged.update(headers or {})
    return requests.request(
        method,
        f"{SERVICE_URLS[service].rstrip('/')}{path}",
        headers=merged,
        timeout=HTTP_TIMEOUT,
        verify=CA_CERT,
        **kwargs,
    )
