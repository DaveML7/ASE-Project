"""Regression tests for the penetration-test findings on the API gateway."""

import importlib.util
import pathlib

import pytest
from ase_common import issue_token

SERVICE = pathlib.Path(__file__).parents[2] / "services" / "gateway" / "app.py"
PLAYER = "11111111-1111-1111-1111-111111111111"


class FakeResponse:
    status_code = 200
    content = b"{}"
    headers = {"Content-Type": "application/json"}


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse()


@pytest.fixture
def gateway(monkeypatch):
    for name in ("INTERNAL_API_KEY", "JWT_SECRET"):
        monkeypatch.setenv(name, "g" * 48)
    from ase_common import secrets

    secrets.read_secret.cache_clear()
    spec = importlib.util.spec_from_file_location("gateway_app", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = FakeSession()
    monkeypatch.setattr(module, "peer_session", lambda: session)
    module.session = session
    return module


@pytest.fixture
def auth_header():
    return {"Authorization": f"Bearer {issue_token(PLAYER, 'ash')}"}


@pytest.mark.parametrize(
    "path",
    [
        "players/../internal/players/x/monsters",
        "players/%2e%2e/internal/players/x/monsters",
        "players/..%2finternal/players/x/monsters",
        "teams/../internal/teams/1",
        "players/me/../../internal/players",
        "monsters/../health",
        "teams/1/../../auth/me",
        "teams//1",
        "teams/1;x",
        "teams/1%00",
        "players/internal/players",
        "history/1/extra",
    ],
)
def test_path_tricks_never_reach_a_service(gateway, auth_header, path):
    response = gateway.app.test_client().get(f"/api/v1/{path}", headers=auth_header)
    # 308 = Werkzeug merging duplicate slashes; the client is redirected, nothing is forwarded
    assert response.status_code in (404, 308)
    assert gateway.session.calls == []


def test_unknown_service_is_404(gateway, auth_header):
    assert gateway.app.test_client().get("/api/v1/db-manager/db/players", headers=auth_header).status_code == 404
    assert gateway.session.calls == []


def test_method_not_allowed_is_405_and_not_forwarded(gateway, auth_header):
    assert gateway.app.test_client().delete("/api/v1/players/me", headers=auth_header).status_code == 405
    assert gateway.session.calls == []


def test_internal_key_is_never_forwarded(gateway, auth_header):
    response = gateway.app.test_client().get("/api/v1/players/me", headers=auth_header)
    assert response.status_code == 200
    forwarded = gateway.session.calls[0]["headers"]
    assert "X-Internal-Key" not in forwarded
    assert forwarded["Authorization"] == auth_header["Authorization"]
    assert gateway.session.calls[0]["url"] == "https://players:5002/players/me"


def test_client_cannot_inject_internal_headers(gateway, auth_header):
    gateway.app.test_client().get(
        "/api/v1/players/me", headers={**auth_header, "X-Internal-Key": "x", "X-Player-Id": "y"}
    )
    assert set(gateway.session.calls[0]["headers"]) <= {"Authorization", "Content-Type", "Accept"}


def test_protected_routes_need_a_token_but_the_catalogue_is_public(gateway):
    client = gateway.app.test_client()
    assert client.get("/api/v1/players/me").status_code == 401
    assert client.get("/api/v1/players/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/v1/monsters/3/image").status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": "a", "password": "b"}).status_code == 200


def test_login_is_rate_limited_per_user(gateway):
    client = gateway.app.test_client()
    statuses = [
        client.post("/api/v1/auth/login", json={"username": "victim", "password": f"guess{i}"}).status_code
        for i in range(gateway.LIMITS["login_user"] + 3)
    ]
    assert statuses[: gateway.LIMITS["login_user"]] == [200] * gateway.LIMITS["login_user"]
    assert statuses[-1] == 429
    blocked = client.post("/api/v1/auth/login", json={"username": "victim", "password": "x"})
    assert int(blocked.headers["Retry-After"]) >= 1
    # another account is not affected by the throttle on this one
    assert client.post("/api/v1/auth/login", json={"username": "other", "password": "x"}).status_code == 200


def test_encounters_are_rate_limited_per_player(gateway, auth_header):
    client = gateway.app.test_client()
    limit = gateway.LIMITS["encounter"]
    codes = [client.post("/api/v1/encounters", json={}, headers=auth_header).status_code for _ in range(limit + 1)]
    assert codes[-1] == 429 and codes[:limit] == [200] * limit


def test_responses_carry_security_headers(gateway):
    response = gateway.app.test_client().get("/health")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "max-age" in response.headers["Strict-Transport-Security"]
    assert response.headers["Content-Security-Policy"].startswith("default-src 'none'")
