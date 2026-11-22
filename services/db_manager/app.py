"""db-manager: the only service allowed to touch PostgreSQL.

Exposes generic CRUD plus a transactional batch endpoint. Callers never send
SQL: table names and columns are checked against the allowlists below.
"""

from contextlib import contextmanager
from threading import Lock
from uuid import UUID

import psycopg2
from ase_common import create_app, read_secret, require_service
from field_crypto import CryptoError, FieldCipher
from flask import abort, g, jsonify, request
from psycopg2 import errorcodes, pool
from psycopg2.extras import Json, RealDictCursor

app = create_app(
    "db-manager",
    required_secrets=("POSTGRES_PASSWORD", "DATA_ENCRYPTION_KEY"),
    required_keys=(),  # it only verifies callers; it signs nothing and never sees user tokens
)
cipher = FieldCipher.from_base64(read_secret("DATA_ENCRYPTION_KEY", min_length=44))

# Columns each service may write. Anything else is rejected with 400.
WRITABLE = {
    "players": {"id", "username", "avatar", "faction"},
    "credentials": {"player_id", "username", "password_hash", "registration_complete"},
    "monsters": {"name", "element", "hp", "attack", "defence", "rarity", "description"},
    "player_monsters": {"player_id", "monster_id", "nickname", "level", "source"},
    "teams": {"player_id", "name"},
    "team_slots": {"team_id", "slot", "player_monster_id"},
    "encounter_templates": {"name", "wild_monster_id", "difficulty", "description"},
    "encounters": {"player_id", "team_id", "template_id", "wild_monster_id", "outcome", "details"},
    "revoked_tokens": {"jti", "player_id", "expires_at"},
}

# Who may do what. The caller is the service proven by its signature (require_service),
# so a compromised service gets ONLY the rights of its own role, never the whole database.
CALLERS = ("auth", "players", "monsters", "teams", "encounters", "history")
ACCESS = {
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


def _authorize(table: str, action: str) -> None:
    if action not in ACCESS.get(g.caller, {}).get(table, set()):
        app.logger.warning("denied: %s may not %s %s", g.caller, action, table)
        abort(403, f"{g.caller} may not {action} {table}")


# Columns stored encrypted (AES-256-GCM). They are transparently decrypted on
# read and can never be used as a filter: ciphertexts are randomised.
ENCRYPTED = {
    "credentials": {"password_hash"},
}
# Numeric columns that may be changed atomically with `col = col + n`.
INCREMENTABLE = {
    "players": {"xp"},
}
MAX_INCREMENT = 10_000
MAX_IN_VALUES = 200
UPDATE_COLUMNS = {
    "players": {"avatar", "faction"},
    "credentials": {"registration_complete"},
    "teams": {"name"},
}
FILTERABLE = {
    table: (columns | {"id"}) - ENCRYPTED.get(table, set()) for table, columns in WRITABLE.items()
}
for table in ("players", "credentials", "teams", "encounters"):
    FILTERABLE[table].add("created_at")
FILTERABLE["players"].update({"level", "xp"})
FILTERABLE["player_monsters"].add("acquired_at")
INTEGER_FILTERS = {
    "players": {"level", "xp"},
    "credentials": {"id"},
    "monsters": {"id", "hp", "attack", "defence"},
    "player_monsters": {"id", "monster_id", "level"},
    "teams": {"id"},
    "team_slots": {"id", "team_id", "slot", "player_monster_id"},
    "encounter_templates": {"id", "wild_monster_id", "difficulty"},
    "encounters": {"id", "team_id", "template_id", "wild_monster_id"},
    "revoked_tokens": {"id"},
}

_connection_pool: pool.ThreadedConnectionPool | None = None
_pool_lock = Lock()


def get_pool() -> pool.ThreadedConnectionPool:
    global _connection_pool
    with _pool_lock:
        if _connection_pool is None:
            # libpq reads PGHOST/PGPORT/PGDATABASE/PGUSER from the env; the password
            # comes from the secrets volume, never from an environment variable.
            _connection_pool = pool.ThreadedConnectionPool(
                1,
                10,
                password=read_secret("POSTGRES_PASSWORD", min_length=16),
                sslmode="verify-full",
                sslrootcert="/run/app-config/ca.crt",
                connect_timeout=3,
                options="-c statement_timeout=5000 -c lock_timeout=3000",
            )
        return _connection_pool


@contextmanager
def connection():
    conn = get_pool().getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            yield cursor
        conn.commit()
    except Exception:
        if not conn.closed:
            conn.rollback()
        raise
    finally:
        get_pool().putconn(conn)


# SQL safety note (the bandit B608 warnings on the statements below are suppressed
# on purpose): table and column names are interpolated into the SQL text, but ONLY after
# _authorize and argument validation checked them against the allowlists above.
# Every value is passed as a bound parameter (%s), never formatted.
def _check_columns(table: str, columns, allowed: dict) -> None:
    unknown = [column for column in columns if column not in allowed[table]]
    if unknown:
        abort(400, f"unknown column(s) for '{table}': {', '.join(unknown)}")


def _object(value, field):
    if not isinstance(value, dict):
        abort(400, f"{field} must be an object")
    return value


def _row_id(table, value):
    if table == "players":
        if isinstance(value, str):
            try:
                return str(UUID(value))
            except ValueError:
                pass
    elif isinstance(value, (str, int)) and not isinstance(value, bool):
        try:
            if 0 < int(value) < 2**63:
                return int(value)
        except ValueError:
            pass
    abort(400, f"invalid row id for '{table}'")


def _write_values(table, values, allowed):
    _object(values, "values")
    if not values:
        abort(400, "values must not be empty")
    _check_columns(table, values, allowed)
    if any(
        isinstance(value, (dict, list))
        for key, value in values.items()
        if (table, key) != ("encounters", "details")
    ):
        abort(400, "only encounters.details accepts objects and arrays")
    if "password_hash" in values and not isinstance(values["password_hash"], str):
        abort(400, "password_hash must be a string")
    if "registration_complete" in values and not isinstance(values["registration_complete"], bool):
        abort(400, "registration_complete must be a boolean")
    return values


def _value(value):
    return Json(value) if isinstance(value, (dict, list)) else value


def _protect(table: str, values: dict) -> dict:
    """Encrypt the sensitive columns of a row about to be written."""
    sensitive = ENCRYPTED.get(table, set())
    protected = dict(values)
    for column in sensitive & protected.keys():
        protected[column] = cipher.encrypt(protected[column], f"{table}.{column}")
    return protected


def _reveal(table: str, row: dict | None) -> dict | None:
    """Decrypt the sensitive columns of a row read from the database."""
    if row is None:
        return None
    revealed = dict(row)
    for column in ENCRYPTED.get(table, set()) & revealed.keys():
        revealed[column] = cipher.decrypt(revealed[column], f"{table}.{column}")
    return revealed


def _insert(cursor, table: str, values: dict) -> dict:
    values = _protect(table, values)
    columns = list(values)
    placeholders = ", ".join(["%s"] * len(columns))
    cursor.execute(
        f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({placeholders}) RETURNING *',  # nosec B608
        [_value(values[column]) for column in columns],
    )
    return _reveal(table, dict(cursor.fetchone()))


def _where(table: str, params: dict) -> tuple[list[str], list]:
    where, arguments = [], []
    for key, value in params.items():
        op, _, column = key.partition(".")
        if op not in {"eq", "gt", "in"}:
            abort(400, f"unknown filter '{key}'")
        _check_columns(table, [column], FILTERABLE)
        if op == "in":
            if column not in INTEGER_FILTERS[table] or not isinstance(value, str):
                abort(400, "in filters require an integer column and a comma-separated string")
            try:
                values = [int(item) for item in value.split(",")] if value else []
            except ValueError:
                abort(400, f"in.{column} must be a comma-separated list of integers")
            if len(values) > MAX_IN_VALUES or any(not -(2**63) <= item < 2**63 for item in values):
                abort(400, f"in.{column} supports at most {MAX_IN_VALUES} signed 64-bit integers")
            where.append(f'"{column}" = ANY(%s)')
            arguments.append(values)
        else:
            if not isinstance(value, (str, int, float)):
                abort(400, f"'{key}' must be a scalar")
            where.append(f'"{column}" {"=" if op == "eq" else ">"} %s')
            arguments.append(str(value))
    return where, arguments


def _query_args(table, params, *, count=False):
    params = _object(params, "filters").copy()
    if count:
        return _where(table, params)
    order_by = params.pop("order_by", "id")
    if not isinstance(order_by, str):
        abort(400, "order_by must be a string")
    order_column = order_by.removeprefix("-")
    _check_columns(table, [order_column], FILTERABLE)
    limit = params.pop("limit", 200)
    try:
        if not isinstance(limit, (int, str)) or isinstance(limit, bool):
            raise ValueError
        limit = max(1, min(int(limit), 500))
    except ValueError:
        abort(400, "limit must be an integer")
    where, arguments = _where(table, params)
    return where, arguments, order_column, order_by.startswith("-"), limit


def _select(cursor, table: str, query) -> list[dict]:
    where, arguments, order_column, descending, limit = query

    sql = f'SELECT * FROM "{table}"'  # nosec B608
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f' ORDER BY "{order_column}" {"DESC" if descending else "ASC"} LIMIT %s'  # nosec B608
    cursor.execute(sql, [*arguments, limit])
    return [_reveal(table, dict(row)) for row in cursor.fetchall()]


def _count(cursor, table: str, query) -> int:
    where, arguments = query
    sql = f'SELECT count(*) AS n FROM "{table}"'  # nosec B608
    if where:
        sql += " WHERE " + " AND ".join(where)
    cursor.execute(sql, arguments)
    return cursor.fetchone()["n"]


def _select_one(cursor, table: str, row_id) -> dict | None:
    cursor.execute(f'SELECT * FROM "{table}" WHERE id = %s', [row_id])  # nosec B608
    row = cursor.fetchone()
    return _reveal(table, dict(row)) if row else None


def _update(cursor, table: str, row_id, values: dict) -> dict | None:
    assignments = ", ".join(f'"{column}" = %s' for column in values)
    cursor.execute(
        f'UPDATE "{table}" SET {assignments} WHERE id = %s RETURNING *',  # nosec B608
        [_value(value) for value in values.values()] + [row_id],
    )
    row = cursor.fetchone()
    return _reveal(table, dict(row)) if row else None


def _increment_args(table, column, by):
    if not isinstance(column, str) or column not in INCREMENTABLE.get(table, set()):
        abort(400, f"'{table}.{column}' cannot be incremented")
    if not isinstance(by, int) or isinstance(by, bool) or abs(by) > MAX_INCREMENT:
        abort(400, f"'by' must be an integer between -{MAX_INCREMENT} and {MAX_INCREMENT}")
    return column, by


def _increment(cursor, table: str, row_id, column, by) -> dict | None:
    cursor.execute(
        f'UPDATE "{table}" SET "{column}" = "{column}" + %s WHERE id = %s RETURNING *',  # nosec B608
        [by, row_id],
    )
    row = cursor.fetchone()
    return _reveal(table, dict(row)) if row else None


def _delete(cursor, table: str, row_id) -> bool:
    cursor.execute(f'DELETE FROM "{table}" WHERE id = %s', [row_id])  # nosec B608
    return cursor.rowcount > 0


@app.errorhandler(CryptoError)
def crypto_error(exception: CryptoError):
    app.logger.error("field encryption failure: %s", exception)
    return jsonify(error="Internal Server Error", message="stored data could not be decrypted"), 500


@app.errorhandler(psycopg2.Error)
def database_error(exception: psycopg2.Error):
    code = exception.pgcode or ""
    if code.startswith("22"):
        return jsonify(error="Bad Request", message="invalid database value"), 400
    if code.startswith("23"):
        constraint = exception.diag.constraint_name
        return jsonify(
            error="Conflict", message="database constraint violation", constraint=constraint
        ), 409
    app.logger.warning("database operation failed: %s (%s)", type(exception).__name__, code)
    if isinstance(
        exception, (psycopg2.OperationalError, psycopg2.InterfaceError, pool.PoolError)
    ) or code in {
        errorcodes.LOCK_NOT_AVAILABLE,
        errorcodes.QUERY_CANCELED,
        errorcodes.DEADLOCK_DETECTED,
        errorcodes.SERIALIZATION_FAILURE,
    }:
        return jsonify(error="Service Unavailable", message="database unavailable"), 503
    return jsonify(error="Internal Server Error", message="database operation failed"), 500


@app.get("/ready")
def ready():
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        abort(404)
    with connection() as cursor:
        cursor.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/db/<table>")
@require_service(*CALLERS)
def select(table: str):
    _authorize(table, "select")
    query = _query_args(table, request.args.to_dict())
    with connection() as cursor:
        return jsonify(_select(cursor, table, query))


@app.get("/db/encounters/history")
@require_service("history")
def encounter_history():
    _authorize("encounters", "select")
    player_id = _row_id("players", request.args.get("player_id"))
    before = request.args.get("before")
    if before is not None:
        before = _row_id("encounters", before)
    try:
        limit = int(request.args.get("limit", 51))
    except ValueError:
        abort(400, "limit must be an integer")
    if not 1 <= limit <= 201:
        abort(400, "limit must be between 1 and 201")

    with connection() as cursor:
        cursor.execute(
            """
            SELECT id, template_id, wild_monster_id, outcome, created_at,
                   jsonb_build_object(
                       'template', details->'template',
                       'wild_monster', jsonb_build_object('name', details->'wild_monster'->'name'),
                       'reward', jsonb_build_object('name', details->'reward'->'name'),
                       'team', CASE WHEN jsonb_typeof(details->'team') = 'array' THEN (
                           SELECT coalesce(jsonb_agg(jsonb_build_object('name', member->'name')),
                                           '[]'::jsonb)
                           FROM jsonb_array_elements(details->'team') AS member
                       ) ELSE '[]'::jsonb END
                   ) AS details  -- only what the list shows; the detail endpoint keeps all
            FROM encounters
            WHERE player_id = %s AND (%s::bigint IS NULL OR (created_at, id) < (
                SELECT created_at, id FROM encounters WHERE id = %s AND player_id = %s
            ))
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            [player_id, before, before, player_id, limit],
        )
        return jsonify(cursor.fetchall())


@app.get("/db/<table>/count")
@require_service(*CALLERS)
def count(table: str):
    _authorize(table, "select")
    query = _query_args(table, request.args.to_dict(), count=True)
    with connection() as cursor:
        return jsonify(count=_count(cursor, table, query))


@app.get("/db/<table>/<row_id>")
@require_service(*CALLERS)
def select_one(table: str, row_id: str):
    _authorize(table, "select")
    row_id = _row_id(table, row_id)
    with connection() as cursor:
        row = _select_one(cursor, table, row_id)
    if row is None:
        abort(404, f"no row {row_id} in {table}")
    return jsonify(row)


@app.post("/db/<table>")
@require_service(*CALLERS)
def insert(table: str):
    _authorize(table, "insert")
    values = _write_values(table, request.get_json(silent=True), WRITABLE)
    with connection() as cursor:
        row = _insert(cursor, table, values)
    return jsonify(row), 201


@app.patch("/db/<table>/<row_id>")
@require_service(*CALLERS)
def update(table: str, row_id: str):
    _authorize(table, "update")
    row_id = _row_id(table, row_id)
    values = _write_values(table, request.get_json(silent=True), UPDATE_COLUMNS)
    with connection() as cursor:
        row = _update(cursor, table, row_id, values)
    if row is None:
        abort(404, f"no row {row_id} in {table}")
    return jsonify(row)


@app.delete("/db/<table>/<row_id>")
@require_service(*CALLERS)
def delete(table: str, row_id: str):
    _authorize(table, "delete")
    row_id = _row_id(table, row_id)
    with connection() as cursor:
        deleted = _delete(cursor, table, row_id)
    if not deleted:
        abort(404, f"no row {row_id} in {table}")
    return "", 204


@app.put("/db/teams/<team_id>/slots")
@require_service("teams")
def replace_team_slots(team_id: str):
    _authorize("teams", "update")
    team_id = _row_id("teams", team_id)
    body = _object(request.get_json(silent=True), "body")
    members = body.get("member_ids")
    if not isinstance(members, list) or not 1 <= len(members) <= 3:
        abort(400, "member_ids must contain one to three IDs")
    if not all(type(member) is int for member in members):
        abort(400, "member IDs must be integers")
    members = [_row_id("player_monsters", member) for member in members]
    if len(set(members)) != len(members):
        abort(400, "member IDs must be distinct")
    name = body.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 40):
        abort(400, "name must be a string of at most 40 characters")

    with connection() as cursor:
        cursor.execute("SELECT * FROM teams WHERE id = %s FOR UPDATE", [team_id])
        team = cursor.fetchone()
        if team is None:
            abort(404, "team not found")
        cursor.execute(
            "SELECT count(*) AS n FROM player_monsters WHERE player_id = %s AND id = ANY(%s)",
            [team["player_id"], members],
        )
        if cursor.fetchone()["n"] != len(members):
            abort(409, "team members must belong to the team owner")
        if name is not None:
            team = _update(cursor, "teams", team_id, {"name": name})
        cursor.execute("DELETE FROM team_slots WHERE team_id = %s", [team_id])
        for slot, member in enumerate(members, start=1):
            _insert(
                cursor,
                "team_slots",
                {"team_id": team_id, "slot": slot, "player_monster_id": member},
            )
    return jsonify(id=team_id, name=team["name"], member_ids=members)


@app.post("/db/batch")
@require_service(*CALLERS)
def batch():
    body = _object(request.get_json(silent=True), "body")
    operations = body.get("operations")
    if not isinstance(operations, list) or not 0 < len(operations) <= 50:
        abort(400, "operations must be a non-empty list of at most 50 entries")

    handlers = {
        "insert": _insert,
        "update": _update,
        "increment": _increment,
        "delete": _delete,
        "select": _select,
    }
    validated = []
    for operation in operations:
        _object(operation, "operation")
        name, table = operation.get("op"), operation.get("table")
        if not isinstance(name, str) or name not in handlers or not isinstance(table, str):
            abort(400, "operation requires a supported op and a table name")
        _authorize(table, name)
        if name == "insert":
            args = (_write_values(table, operation.get("values"), WRITABLE),)
        elif name == "select":
            args = (_query_args(table, operation.get("filters", {})),)
        else:
            args = (_row_id(table, operation.get("id")),)
            if name == "update":
                args += (_write_values(table, operation.get("values"), UPDATE_COLUMNS),)
            elif name == "increment":
                args += _increment_args(table, operation.get("column"), operation.get("by"))
        validated.append((handlers[name], table, args))

    results = []
    with connection() as cursor:
        for handler, table, args in validated:
            result = handler(cursor, table, *args)
            if result is None:
                abort(404, f"no row {args[0]} in {table}")
            results.append(result)
    return jsonify(results), 201
