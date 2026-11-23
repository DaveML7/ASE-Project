"""db-manager authorisation: every service gets only the rights of its own role."""

import pytest
from dbmanager.policy import ACCESS, CALLERS, TABLES, authorize
from werkzeug.exceptions import Forbidden


def allowed(caller, table, action):
    try:
        authorize(caller, table, action)
        return True
    except Forbidden:
        return False


def test_only_auth_can_touch_credentials_and_revocations():
    for service in ("players", "monsters", "teams", "encounters", "history"):
        for action in ("select", "insert", "update", "delete"):
            assert not allowed(service, "credentials", action)
            assert not allowed(service, "revoked_tokens", action)
    assert allowed("auth", "credentials", "insert")
    assert not allowed("auth", "credentials", "delete"), "accounts are never deleted"


def test_a_compromised_monsters_service_can_only_read_the_catalogue():
    rights = {(t, a) for t, acts in ACCESS["monsters"].items() for a in acts}
    assert rights == {("monsters", "select")}


def test_only_encounters_can_change_experience_or_write_results():
    for service in ("auth", "players", "monsters", "teams", "history"):
        assert not allowed(service, "players", "increment")
        assert not allowed(service, "encounters", "insert")
    assert allowed("encounters", "players", "increment")
    assert not allowed("encounters", "players", "update"), "no direct XP/level overwrite"
    assert not allowed("encounters", "encounters", "delete"), "history cannot be erased"
    assert not allowed("history", "encounters", "insert")


def test_collections_are_managed_by_players_and_extended_only_by_rewards():
    assert allowed("players", "players", "update")
    assert allowed("encounters", "player_monsters", "insert")
    assert not allowed("encounters", "player_monsters", "delete")
    assert not allowed("teams", "player_monsters", "insert")


@pytest.mark.parametrize(
    "caller, table, action",
    [
        ("ghost", "monsters", "select"),
        ("auth", "nonexistent", "select"),
        ("auth", "credentials", "drop"),
        ("auth", None, "select"),
        ("auth", "credentials", None),
        (None, "credentials", "select"),
        ("auth", ["credentials"], "select"),
    ],
)
def test_unknown_or_malformed_requests_are_denied(caller, table, action):
    assert not allowed(caller, table, action)


def test_every_policy_table_and_caller_is_known():
    for caller, tables in ACCESS.items():
        assert caller in CALLERS
        assert set(tables) <= set(TABLES), caller


def test_every_table_lives_in_exactly_one_service_schema():
    schemas = {spec.schema for spec in TABLES.values()}
    assert schemas == {
        "auth_data",
        "players_data",
        "monsters_data",
        "teams_data",
        "encounters_data",
    }
    assert all(spec.schema.endswith("_data") for spec in TABLES.values())
