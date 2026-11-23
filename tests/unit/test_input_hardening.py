"""Every handler answers malformed input with a 4xx, never a 500 traceback."""

import importlib.util
import pathlib

import pytest
from ase_common import issue_token

SERVICES = pathlib.Path(__file__).parents[2] / "services"
PLAYER = "11111111-1111-1111-1111-111111111111"
BAD_BODIES = ["[1]", '"text"', "42", "true", "null"]


def load(name, folder):
    spec = importlib.util.spec_from_file_location(f"{name}_app", SERVICES / folder / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def auth_header():
    return {"Authorization": f"Bearer {issue_token(PLAYER, 'ash')}"}


class AuthDb:
    def __init__(self):
        self.selects = []

    def select(self, table, **filters):
        self.selects.append(filters)
        return []

    def insert(self, table, values):
        return {"id": 1, "player_id": values["player_id"]}


@pytest.fixture
def auth(key_dir):
    module = load("auth", "auth")
    module.db = AuthDb()
    return module


@pytest.mark.parametrize("path", ["/auth/register", "/auth/login"])
@pytest.mark.parametrize("body", ["[1]", '"text"', "42", "true"])
def test_auth_rejects_bodies_that_are_not_objects(auth, path, body):
    response = auth.app.test_client().post(path, data=body, content_type="application/json")
    assert response.status_code == 400, response.json


@pytest.mark.parametrize(
    "credentials",
    [
        {"username": ["x"], "password": "longenough1"},
        {"username": "valid_user", "password": ["longenough1"]},
        {"username": {"a": 1}, "password": {"b": 2}},
        {"username": 123456, "password": 12345678},
    ],
)
def test_credentials_must_be_strings_not_their_python_representation(auth, credentials):
    for path in ("/auth/register", "/auth/login"):
        assert auth.app.test_client().post(path, json=credentials).status_code == 400


def test_passwords_have_a_maximum_length(auth):
    long_password = "x" * (auth.MAX_PASSWORD_LENGTH + 1)
    client = auth.app.test_client()
    register = client.post(
        "/auth/register", json={"username": "valid_user", "password": long_password}
    )
    assert register.status_code == 400
    login = client.post("/auth/login", json={"username": "valid_user", "password": long_password})
    assert login.status_code == 401, "refused like any wrong password, without a long scrypt run"


def test_an_unknown_user_costs_a_hash_check_like_a_known_one(auth, monkeypatch):
    calls = []
    monkeypatch.setattr(
        auth, "check_password_hash", lambda stored, password: calls.append(stored) or False
    )
    auth.app.test_client().post("/auth/login", json={"username": "nobody", "password": "whatever1"})
    assert calls == [auth.DUMMY_HASH]


class PlayersDb:
    def __init__(self):
        self.updated = None

    def update(self, table, row_id, values):
        self.updated = values
        return {
            "id": row_id,
            "username": "ash",
            "avatar": values.get("avatar", "x"),
            "faction": values.get("faction"),
            "level": 1,
            "xp": 0,
        }


@pytest.fixture
def players(key_dir):
    module = load("players", "players")
    module.db = PlayersDb()
    return module


@pytest.mark.parametrize("body", BAD_BODIES[:4])
def test_players_rejects_bodies_that_are_not_objects(players, body):
    response = players.app.test_client().patch(
        "/players/me", data=body, content_type="application/json", headers=auth_header()
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"avatar": ["a"]},
        {"avatar": {"a": 1}},
        {"avatar": 7},
        {"avatar": None},
        {"avatar": "  "},
        {"avatar": "x" * 41},
        {"faction": ["f"]},
        {"faction": 5},
        {"faction": "f" * 41},
        {},
    ],
)
def test_players_validates_the_type_of_every_field(players, body):
    response = players.app.test_client().patch("/players/me", json=body, headers=auth_header())
    assert response.status_code == 400
    assert players.db.updated is None


def test_a_null_faction_clears_it_instead_of_storing_the_text_none(players):
    response = players.app.test_client().patch(
        "/players/me", json={"faction": None}, headers=auth_header()
    )
    assert response.status_code == 200 and players.db.updated == {"faction": None}
    assert response.json["faction"] is None


def test_values_are_trimmed_and_blank_faction_is_cleared(players):
    players.app.test_client().patch(
        "/players/me", json={"avatar": "  wizard ", "faction": "   "}, headers=auth_header()
    )
    assert players.db.updated == {"avatar": "wizard", "faction": None}


# ----------------------------------------------------------------- teams, encounters, history
class TeamsDb:
    def get(self, table, row_id):
        return {"id": row_id, "player_id": PLAYER, "name": "t"}

    def select(self, *args, **kwargs):
        return []


@pytest.fixture
def teams(key_dir):
    module = load("teams", "teams")
    module.db = TeamsDb()
    return module


@pytest.fixture
def encounters(key_dir):
    return load("encounters", "encounters")


@pytest.mark.parametrize("body", BAD_BODIES[:4])
def test_teams_and_encounters_reject_bodies_that_are_not_objects(teams, encounters, body):
    for module, method, path in ((teams, "post", "/teams"), (encounters, "post", "/encounters")):
        response = getattr(module.app.test_client(), method)(
            path, data=body, content_type="application/json", headers=auth_header()
        )
        assert response.status_code == 400, (path, response.json)
    update = teams.app.test_client().put(
        "/teams/1", data=body, content_type="application/json", headers=auth_header()
    )
    assert update.status_code in (400, 404)


@pytest.mark.parametrize("limit", ["0", "-1", "abc"])
def test_history_rejects_a_bad_limit_like_every_other_service(key_dir, limit):
    history = load("history", "history")
    assert (
        history.app.test_client().get(f"/history?limit={limit}", headers=auth_header()).status_code
        == 400
    )


# ----------------------------------------------------------------- db-manager pool
def test_pool_exhaustion_and_connection_loss_are_503_not_500(key_dir, monkeypatch):
    import base64
    import os

    from psycopg2 import pool

    monkeypatch.setenv("POSTGRES_PASSWORD", "p" * 40)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("SERVICE_NAME", "db-manager")
    from ase_common import secrets

    secrets.read_secret.cache_clear()
    module = load("db_manager", "db_manager")
    from dbmanager import database

    class Exhausted:
        def getconn(self):
            raise pool.PoolError("connection pool exhausted")

    monkeypatch.setattr(database, "get_pool", lambda: Exhausted())
    response = module.app.test_client().get("/ready")
    assert response.status_code == 503 and response.json["error"] == "Service Unavailable"


def test_the_db_manager_uses_a_thread_safe_pool():
    source = (SERVICES / "db_manager" / "dbmanager" / "database.py").read_text()
    assert "ThreadedConnectionPool" in source and "SimpleConnectionPool" not in source
