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


def test_nul_characters_and_broken_json_are_client_errors():
    """Penetration-test finding: a NUL in a login name reached PostgreSQL and became a 502."""
    from ase_common import create_app, json_object
    from flask import jsonify

    app = create_app("probe", required_keys=())

    @app.post("/probe")
    def probe():
        return jsonify(json_object())

    client = app.test_client()
    assert client.post("/probe", json={"a": "x\u0000y"}).status_code == 400
    assert client.post("/probe?q=a%00b", json={}).status_code == 400
    assert (
        client.post("/probe", data="{not json", content_type="application/json").status_code == 400
    )
    assert client.post("/probe", data="{not json", content_type="text/plain").status_code == 400
    assert client.post("/probe").json == {}
    assert client.post("/probe", json={"ok": 1}).json == {"ok": 1}
    assert client.post("/probe", json=[1]).status_code == 400


def test_ids_outside_the_database_range_are_a_404_not_a_server_error():
    """Fuzzing finding: /teams/0 and /encounters/0 reached PostgreSQL and became a 502."""
    from ase_common import create_app, is_row_id
    from flask import jsonify

    app = create_app("probe2", required_keys=())

    @app.get("/thing/<id:thing_id>")
    def thing(thing_id: int):
        return jsonify(thing_id)

    client = app.test_client()
    assert client.get("/thing/7").json == 7
    for bad in ("0", "-1", "9223372036854775808", "99999999999999999999999", "abc", "1.5"):
        assert client.get(f"/thing/{bad}").status_code == 404, bad
    assert client.get("/thing/9223372036854775807").status_code == 200
    assert is_row_id(1) and not is_row_id(0) and not is_row_id(True) and not is_row_id("1")
    assert not is_row_id(2**63)


def test_errors_raised_by_gunicorn_itself_are_json():
    """Fuzzing finding: an over-long URI got gunicorn's HTML error page."""
    import importlib.util
    import json
    import pathlib

    path = pathlib.Path(__file__).parents[2] / "docker" / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_conf", path)
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)

    class Sock:
        sent = b""

        def sendall(self, data):
            self.sent += data

    sock = Sock()
    conf._json_error(sock, 414, "URI Too Long", "Request Line is too large (5079 > 4094) <x>")
    head, _, body = sock.sent.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 414 URI Too Long")
    assert b"Content-Type: application/json" in head
    assert json.loads(body)["error"] == "URI Too Long"
    assert b"5079" not in body and b"<x>" not in body  # the parser's text is not echoed


def test_usernames_have_one_canonical_spelling(monkeypatch, key_dir):
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).parents[2] / "services" / "auth" / "app.py"
    spec = importlib.util.spec_from_file_location("auth_app_case", path)
    auth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auth)
    with auth.app.test_request_context(
        "/auth/login", method="POST", json={"username": "  AsH_01 ", "password": "x"}
    ):
        assert auth._credentials_body() == ("ash_01", "x")
    with auth.app.test_request_context(
        "/auth/login", method="POST", json={"username": "\u212aevin", "password": "x"}
    ):
        name, _ = auth._credentials_body()
        assert not auth.USERNAME_PATTERN.match(name)  # the Kelvin sign is not folded to "k"


def test_deeply_nested_json_is_a_400_not_a_500():
    """Review finding: json.loads raises RecursionError (not ValueError) on deep nesting."""
    from ase_common import create_app, json_object
    from flask import jsonify

    app = create_app("probe3", required_keys=())

    @app.post("/probe")
    def probe():
        return jsonify(json_object())

    client = app.test_client()
    for depth in (20_000, 60_000):
        response = client.post("/probe", data=b"[" * depth, content_type="application/json")
        assert response.status_code == 400, depth
    assert client.post("/probe", json={"a": [[[1]]]}).json == {"a": [[[1]]]}


def test_only_a_real_nul_escape_is_refused():
    from ase_common import create_app, json_object
    from flask import jsonify

    app = create_app("probe4", required_keys=())

    @app.post("/probe")
    def probe():
        return jsonify(json_object())

    client = app.test_client()
    escaped_backslash = b'{"a": "x\\\\u0000y"}'  # backslash + "u0000": no NUL in the value
    assert client.post("/probe", data=escaped_backslash, content_type="application/json").json
    for body in (b'{"a": "\\u0000"}', b'{"a": "\\\\\\u0000"}', b'{"a": "\\U0000"}'):
        assert client.post("/probe", data=body, content_type="application/json").status_code == 400


def test_a_failed_half_open_trial_does_not_leave_the_circuit_stuck(monkeypatch):
    from ase_common import resilience

    clock = [0.0]
    breaker = resilience.CircuitBreaker("t", failures=1, reset_after=5, clock=lambda: clock[0])
    monkeypatch.setitem(resilience._breakers, "t", breaker)

    class Boom:
        def request(self, *args, **kwargs):
            raise UnicodeEncodeError("latin-1", "x", 0, 1, "bad header")

    with pytest.raises(UnicodeEncodeError):
        resilience.guarded_request(Boom(), "t", "GET", "https://t/")
    clock[0] = 10.0  # half-open: the next call is the trial, and it fails the same way
    with pytest.raises(UnicodeEncodeError):
        resilience.guarded_request(Boom(), "t", "GET", "https://t/")
    clock[0] = 20.0  # without the fix the trial flag stayed set: CircuitOpen for ever
    with pytest.raises(UnicodeEncodeError):
        resilience.guarded_request(Boom(), "t", "GET", "https://t/")


def test_db_manager_outcomes_are_not_reported_as_outages():
    """A vanished row or a database conflict keeps its meaning; only real failures are 502."""
    from ase_common import DbError, create_app

    app = create_app("probe5", required_keys=())

    @app.get("/raise/<int:status>")
    def boom(status: int):
        raise DbError("x", status)

    client = app.test_client()
    assert client.get("/raise/404").status_code == 404
    assert client.get("/raise/409").status_code == 409
    assert client.get("/raise/400").status_code == 502
    assert client.get("/raise/503").status_code == 502
