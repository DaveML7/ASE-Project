"""Who owns which data and who may do what. Pure data: no Flask, no SQL, no I/O.

This module is the single source for both the application checks (`authorize`) and the
database grants (`tools/build_db_access.py` generates db/app-access.sql from it), so the
two cannot drift apart.

Data is split per microservice: every table lives in the schema of the service that owns it
and every calling service runs its queries with its own PostgreSQL role (`svc_<caller>`).
"""

from dataclasses import dataclass

from flask import abort


@dataclass(frozen=True)
class Table:
    schema: str  # owner service's schema
    writable: frozenset  # columns accepted on INSERT
    updatable: frozenset = frozenset()  # columns accepted on PATCH
    encrypted: frozenset = frozenset()  # stored AES-256-GCM encrypted, never filterable
    incrementable: frozenset = frozenset()  # columns changed with `col = col + n`
    returned_on_increment: tuple = ()  # columns an increment may read back
    extra_filters: frozenset = frozenset()  # filterable columns beyond writable + id
    integer_filters: frozenset = frozenset()  # columns that accept `in.` filters

    @property
    def filterable(self) -> frozenset:
        return (self.writable | {"id"} | self.extra_filters) - self.encrypted


def _t(schema, writable, **kwargs):
    kwargs = {k: frozenset(v) if isinstance(v, (set, list)) else v for k, v in kwargs.items()}
    return Table(schema, frozenset(writable), **kwargs)


TABLES: dict[str, Table] = {
    "credentials": _t(
        "auth_data",
        {"player_id", "username", "password_hash", "registration_complete"},
        updatable={"registration_complete"},
        encrypted={"password_hash"},
        extra_filters={"created_at"},
        integer_filters={"id"},
    ),
    "revoked_tokens": _t("auth_data", {"jti", "player_id", "expires_at"}, integer_filters={"id"}),
    "players": _t(
        "players_data",
        {"id", "username", "avatar", "faction"},
        updatable={"avatar", "faction"},
        incrementable={"xp"},
        returned_on_increment=("id", "xp", "level"),
        extra_filters={"created_at", "level", "xp"},
        integer_filters={"level", "xp"},
    ),
    "player_monsters": _t(
        "players_data",
        {"player_id", "monster_id", "nickname", "level", "source"},
        extra_filters={"acquired_at"},
        integer_filters={"id", "monster_id", "level"},
    ),
    "monsters": _t(
        "monsters_data",
        {"name", "element", "hp", "attack", "defence", "rarity", "description"},
        integer_filters={"id", "hp", "attack", "defence"},
    ),
    "teams": _t(
        "teams_data",
        {"player_id", "name"},
        updatable={"name"},
        extra_filters={"created_at"},
        integer_filters={"id"},
    ),
    "team_slots": _t(
        "teams_data",
        {"team_id", "slot", "player_monster_id"},
        integer_filters={"id", "team_id", "slot", "player_monster_id"},
    ),
    "encounter_templates": _t(
        "encounters_data",
        {"name", "wild_monster_id", "difficulty", "description"},
        integer_filters={"id", "wild_monster_id", "difficulty"},
    ),
    "encounters": _t(
        "encounters_data",
        {"player_id", "team_id", "template_id", "wild_monster_id", "outcome", "details"},
        extra_filters={"created_at"},
        integer_filters={"id", "team_id", "template_id", "wild_monster_id"},
    ),
}

CALLERS = ("auth", "players", "monsters", "teams", "encounters", "history")

# What each service may ask the db-manager to do through the generic endpoints.
ACCESS: dict[str, dict[str, set[str]]] = {
    "auth": {
        "credentials": {"select", "insert", "update"},
        "revoked_tokens": {"select", "insert"},
    },
    "players": {
        "players": {"select", "insert", "update"},
        "player_monsters": {"select", "insert"},
        "monsters": {"select"},
    },
    "monsters": {"monsters": {"select"}},
    "teams": {
        "teams": {"select", "insert", "update", "delete"},
        "team_slots": {"select"},
    },
    "encounters": {
        "encounter_templates": {"select"},
        "monsters": {"select"},
        "encounters": {"select", "insert"},
        "player_monsters": {"insert"},
        "players": {"increment"},
    },
    "history": {"encounters": {"select"}},
}

# Extra rights used only by dedicated endpoints (never by the generic ones): replacing the
# slots of a team inside one transaction, which also checks that the monsters are owned.
DEDICATED_RIGHTS: dict[str, dict[str, set[str]]] = {
    "teams": {"team_slots": {"insert", "delete"}, "player_monsters": {"select"}},
    "auth": {"revoked_tokens": {"delete"}},  # purge of expired entries only (see dedicated.py)
}

ACTIONS = {"select", "insert", "update", "increment", "delete"}


def role_for(caller: str) -> str:
    """The PostgreSQL role a caller's statements run as."""
    return f"svc_{caller}"


def authorize(caller: str, table, action) -> None:
    if (
        not isinstance(table, str)
        or not isinstance(action, str)
        or action not in ACCESS.get(caller, {}).get(table, set())
    ):
        abort(403, f"{caller} may not {action} {table}")


def table_spec(table: str) -> Table:
    spec = TABLES.get(table)
    if spec is None:
        abort(403, f"unknown table '{table}'")
    return spec
