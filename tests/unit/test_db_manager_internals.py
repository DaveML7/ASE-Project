"""The db-manager's pure layers: validation, SQL building and the generated database grants."""

import importlib.util
import pathlib

import pytest
from dbmanager import sql as q
from dbmanager import validation as v
from dbmanager.policy import TABLES
from flask import Flask
from werkzeug.exceptions import BadRequest

ROOT = pathlib.Path(__file__).parents[2]


@pytest.fixture
def flask_context():
    with Flask("t").test_request_context():
        yield


def spec(name):
    return TABLES[name]


# ----------------------------------------------------------------------------- validation
@pytest.mark.parametrize(
    "table, value",
    [
        ("monsters", "0"),
        ("monsters", -1),
        ("monsters", "abc"),
        ("monsters", True),
        ("monsters", 2**63),
        ("players", "not-a-uuid"),
        ("players", 5),
        ("teams", None),
    ],
)
def test_bad_row_ids_are_rejected(flask_context, table, value):
    with pytest.raises(BadRequest):
        v.row_id(table, value)


def test_valid_row_ids_are_normalised(flask_context):
    assert v.row_id("monsters", "7") == 7
    assert v.row_id("players", "11111111-1111-1111-1111-111111111111").count("-") == 4


@pytest.mark.parametrize(
    "params",
    [
        {"eq.nope": "1"},
        {"eq.password_hash": "x"},
        {"zz.id": "1"},
        {"in.name": "a,b"},
        {"in.id": "1,x"},
        {"eq.id": [1]},
        {"order_by": "password_hash"},
        {"limit": "x"},
    ],
)
def test_hostile_queries_are_rejected(flask_context, params):
    table = "credentials" if "password_hash" in str(params) else "monsters"
    with pytest.raises(BadRequest):
        v.parse_query(table, spec(table), params)


def test_in_filters_are_capped(flask_context):
    too_many = ",".join(str(n) for n in range(v.MAX_IN_VALUES + 1))
    with pytest.raises(BadRequest):
        v.parse_query("monsters", spec("monsters"), {"in.id": too_many})


def test_a_query_is_parsed_into_typed_filters(flask_context):
    query = v.parse_query(
        "encounters",
        spec("encounters"),
        {"eq.player_id": "p", "in.id": "1,2", "order_by": "-id", "limit": "9999"},
    )
    assert query.limit == v.MAX_LIMIT and query.descending and query.order_column == "id"
    assert {(f.op, f.column) for f in query.filters} == {("eq", "player_id"), ("in", "id")}


def test_only_declared_columns_can_be_written_or_updated(flask_context):
    with pytest.raises(BadRequest):
        v.write_values("players", spec("players"), {"xp": 99999}, spec("players").writable)
    with pytest.raises(BadRequest):
        v.write_values("players", spec("players"), {"username": "x"}, spec("players").updatable)
    with pytest.raises(BadRequest):
        v.write_values("monsters", spec("monsters"), {"name": {"a": 1}}, spec("monsters").writable)
    assert v.write_values(
        "encounters", spec("encounters"), {"details": {"a": 1}}, spec("encounters").writable
    )


def test_increments_are_limited_to_declared_columns_and_amounts(flask_context):
    assert v.increment_args("players", spec("players"), "xp", 20) == ("xp", 20)
    for column, by in (("level", 1), ("xp", 10**9), ("xp", True), ("xp", "5"), (None, 1)):
        with pytest.raises(BadRequest):
            v.increment_args("players", spec("players"), column, by)


# ----------------------------------------------------------------------------- SQL building
def test_user_values_only_ever_travel_as_parameters():
    attack = "1; DROP TABLE monsters_data.monsters; --"
    query = v.Query((v.Filter("eq", "name", attack),), "id", False, 10)
    text, params = q.select("monsters", query)
    assert attack in params and attack not in repr(text)
    assert "LIMIT" in repr(text) and params[-1] == 10


def test_identifiers_are_schema_qualified_per_owner():
    assert "monsters_data" in repr(q.table_ref("monsters"))
    assert "auth_data" in repr(q.table_ref("credentials"))
    with pytest.raises(KeyError):
        q.table_ref("pg_catalog.pg_user")


def test_every_statement_builder_yields_composable_sql():
    for builder, args in (
        (q.insert, ("monsters", ["name"])),
        (q.select_one, ("monsters",)),
        (q.update, ("teams", ["name"])),
        (q.increment, ("players", "xp", ("id", "xp", "level"))),
        (q.delete, ("teams",)),
    ):
        assert repr(builder(*args)).startswith("Composed")


# ----------------------------------------------------------------------------- generated grants
def load_generator():
    spec_ = importlib.util.spec_from_file_location(
        "build_db_access", ROOT / "tools" / "build_db_access.py"
    )
    module = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(module)
    return module


def test_the_committed_database_grants_match_the_policy():
    assert (
        ROOT / "db" / "app-access.sql"
    ).read_text() == load_generator().build(), "run `python tools/build_db_access.py`"


def granted(role_caller):
    return "\n".join(load_generator().grants(role_caller))


def test_a_service_role_gets_no_privilege_in_another_services_schema():
    assert "auth_data" not in granted("monsters")
    assert "players_data" not in granted("monsters")
    assert "auth_data" not in granted("teams") and "encounters_data" not in granted("teams")
    assert "auth_data" not in granted("players") and "auth_data" not in granted("encounters")
    assert "auth_data" not in granted("history") and "players_data" not in granted("history")


def test_only_the_owner_can_write_its_most_sensitive_tables():
    gen = load_generator()
    for caller in gen.CALLERS:
        text = granted(caller)
        if caller != "auth":
            assert "credentials" not in text and "revoked_tokens" not in text
        assert "DELETE ON encounters_data" not in text, "history can never be erased"
    assert "GRANT UPDATE (xp)" in granted("encounters")
    assert "GRANT UPDATE (xp)" not in granted("players"), "only encounters can add experience"


def test_the_login_role_keeps_no_table_rights_of_its_own():
    text = (ROOT / "db" / "app-access.sql").read_text()
    assert "WITH INHERIT FALSE, SET TRUE" in text
    assert "TO monster_tamers_app;" not in text.replace("GRANT svc_", "")  # no direct table grant
