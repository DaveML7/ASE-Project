"""db-manager authorisation: every service gets only the rights of its own role."""

import base64
import importlib.util
import os
import pathlib

import pytest
from flask import g
from werkzeug.exceptions import BadRequest, Forbidden

SERVICE = pathlib.Path(__file__).parents[2] / "services" / "db_manager" / "app.py"


@pytest.fixture
def db_manager(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "p" * 40)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("SERVICE_NAME", "db-manager")
    from ase_common import secrets

    secrets.read_secret.cache_clear()
    spec = importlib.util.spec_from_file_location("db_manager_app", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def allowed(module, caller, table, action):
    with module.app.test_request_context():
        g.caller = caller
        try:
            module._authorize(table, action)
            return True
        except Forbidden:
            return False


def test_only_auth_can_touch_credentials_and_revocations(db_manager):
    for service in ("players", "monsters", "teams", "encounters", "history"):
        for action in ("select", "insert", "update", "delete"):
            assert not allowed(db_manager, service, "credentials", action)
            assert not allowed(db_manager, service, "revoked_tokens", action)
    assert allowed(db_manager, "auth", "credentials", "insert")
    assert allowed(db_manager, "auth", "credentials", "update")  # registration status only
    with db_manager.app.test_request_context(), pytest.raises(BadRequest):
        db_manager._write_values("credentials", {"password_hash": "not allowed"}, db_manager.UPDATE_COLUMNS)


def test_a_compromised_monsters_service_can_only_read_the_catalogue(db_manager):
    rights = {(t, a) for t, acts in db_manager.ACCESS["monsters"].items() for a in acts}
    assert rights == {("monsters", "select")}


def test_only_encounters_can_change_experience_or_write_results(db_manager):
    for service in ("auth", "players", "monsters", "teams", "history"):
        assert not allowed(db_manager, service, "players", "increment")
        assert not allowed(db_manager, service, "encounters", "insert")
    assert allowed(db_manager, "encounters", "players", "increment")
    assert not allowed(db_manager, "encounters", "players", "update"), "no direct XP/level overwrite"
    assert not allowed(db_manager, "encounters", "encounters", "delete"), "history cannot be erased"
    assert not allowed(db_manager, "history", "encounters", "insert")


def test_nobody_but_players_service_manages_profiles_and_collections_except_rewards(db_manager):
    assert allowed(db_manager, "players", "players", "update")
    assert allowed(db_manager, "encounters", "player_monsters", "insert")
    assert not allowed(db_manager, "encounters", "player_monsters", "delete")
    assert not allowed(db_manager, "teams", "player_monsters", "insert")


def test_unknown_caller_table_or_action_is_denied(db_manager):
    assert not allowed(db_manager, "ghost", "monsters", "select")
    assert not allowed(db_manager, "auth", "nonexistent", "select")
    assert not allowed(db_manager, "auth", "credentials", "drop")
    assert not allowed(db_manager, "auth", None, "select")


@pytest.mark.parametrize("filters", [
    {"player_id": "not a valid filter name"},
    {"eq.acquired_at": "not a players column"},
    {"in.xp": ",".join(map(str, range(201)))},
    {"limit": []},
])
def test_invalid_filters_are_rejected(db_manager, filters):
    with pytest.raises(BadRequest):
        db_manager._query_args("players", filters)


def test_every_acl_table_exists_in_the_allowlist(db_manager):
    for caller, tables in db_manager.ACCESS.items():
        assert set(tables) <= set(db_manager.WRITABLE), caller
