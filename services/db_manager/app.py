"""db-manager: the only service allowed to touch PostgreSQL.

Exposes generic CRUD plus a transactional batch endpoint. Callers never send
SQL: table names and columns are checked against the allowlists below.
"""

from contextlib import contextmanager

import psycopg2
from ase_common import create_app, read_secret, require_internal
from field_crypto import CryptoError, FieldCipher
from flask import abort, jsonify, request
from psycopg2 import pool
from psycopg2.extras import Json, RealDictCursor

app = create_app("db-manager", required_secrets=("INTERNAL_API_KEY", "POSTGRES_PASSWORD", "DATA_ENCRYPTION_KEY"))
cipher = FieldCipher.from_base64(read_secret("DATA_ENCRYPTION_KEY", min_length=44))

# Columns each service may write. Anything else is rejected with 400.
WRITABLE = {
    "players": {"id", "username", "avatar", "faction", "xp"},  # level is derived from xp by the database
    "credentials": {"player_id", "username", "password_hash"},
    "monsters": {"name", "element", "hp", "attack", "defence", "rarity", "description"},
    "player_monsters": {"player_id", "monster_id", "nickname", "level", "source"},
    "teams": {"player_id", "name"},
    "team_slots": {"team_id", "slot", "player_monster_id"},
    "encounter_templates": {"name", "wild_monster_id", "difficulty", "description"},
    "encounters": {"player_id", "team_id", "template_id", "wild_monster_id", "outcome", "details"},
}
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
FILTERABLE = {
    table: (columns | {"id", "created_at", "acquired_at"}) - ENCRYPTED.get(table, set())
    for table, columns in WRITABLE.items()
}
FILTERABLE["players"] = FILTERABLE["players"] | {"level"}

_connection_pool: pool.SimpleConnectionPool | None = None


def get_pool() -> pool.SimpleConnectionPool:
    global _connection_pool
    if _connection_pool is None:
        # libpq reads PGHOST/PGPORT/PGDATABASE/PGUSER from the env; the password
        # comes from the secrets volume, never from an environment variable.
        _connection_pool = pool.SimpleConnectionPool(
            1, 10, password=read_secret("POSTGRES_PASSWORD", min_length=16)
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
        conn.rollback()
        raise
    finally:
        get_pool().putconn(conn)


# SQL safety note (the bandit B608 warnings on the statements below are suppressed
# on purpose): table and column names are interpolated into the SQL text, but ONLY after
# _check_table/_check_columns/_increment verified them against the allowlists at the
# top of this file. Every value is passed as a bound parameter (%s), never formatted.
def _check_table(table: str) -> None:
    if table not in WRITABLE:
        abort(404, f"unknown table '{table}'")


def _check_columns(table: str, columns, allowed: dict) -> None:
    unknown = [column for column in columns if column not in allowed[table]]
    if unknown:
        abort(400, f"unknown column(s) for '{table}': {', '.join(unknown)}")


def _value(value):
    return Json(value) if isinstance(value, (dict, list)) else value


def _protect(table: str, values: dict) -> dict:
    """Encrypt the sensitive columns of a row about to be written."""
    sensitive = ENCRYPTED.get(table, set())
    protected = dict(values)
    for column in sensitive & protected.keys():
        if not isinstance(protected[column], str):
            abort(400, f"'{column}' must be a string")
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
    _check_table(table)
    if not values:
        abort(400, "values must not be empty")
    _check_columns(table, values, WRITABLE)
    values = _protect(table, values)
    columns = list(values)
    placeholders = ", ".join(["%s"] * len(columns))
    cursor.execute(
        f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({placeholders}) RETURNING *',  # nosec B608
        [_value(values[column]) for column in columns],
    )
    return _reveal(table, dict(cursor.fetchone()))


def _where(table: str, params: dict) -> tuple[list[str], list]:
    """WHERE fragments for `eq.<col>=v` and `in.<col>=1,2,3` (integers only) filters."""
    where, arguments = [], []
    for key, value in params.items():
        if key.startswith("eq."):
            column = key.removeprefix("eq.")
            _check_columns(table, [column], FILTERABLE)
            where.append(f'"{column}" = %s')
            arguments.append(value)
        elif key.startswith("in."):
            column = key.removeprefix("in.")
            _check_columns(table, [column], FILTERABLE)
            try:
                values = [int(item) for item in value.split(",") if item][:MAX_IN_VALUES]
            except ValueError:
                abort(400, f"in.{column} must be a comma-separated list of integers")
            where.append(f'"{column}" = ANY(%s)')
            arguments.append(values)
    return where, arguments


def _select(cursor, table: str, params: dict) -> list[dict]:
    _check_table(table)
    where, arguments = _where(table, params)

    order_by = str(params.get("order_by", "id"))
    descending = order_by.startswith("-")
    order_column = order_by.lstrip("-")
    _check_columns(table, [order_column], FILTERABLE)
    try:
        limit = max(1, min(int(params.get("limit", 200)), 500))
    except ValueError:
        abort(400, "limit must be an integer")

    sql = f'SELECT * FROM "{table}"'  # nosec B608
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f' ORDER BY "{order_column}" {"DESC" if descending else "ASC"} LIMIT %s'  # nosec B608
    cursor.execute(sql, [*arguments, limit])
    return [_reveal(table, dict(row)) for row in cursor.fetchall()]


def _count(cursor, table: str, params: dict) -> int:
    _check_table(table)
    where, arguments = _where(table, params)
    sql = f'SELECT count(*) AS n FROM "{table}"'  # nosec B608
    if where:
        sql += " WHERE " + " AND ".join(where)
    cursor.execute(sql, arguments)
    return cursor.fetchone()["n"]


def _select_one(cursor, table: str, row_id) -> dict | None:
    _check_table(table)
    cursor.execute(f'SELECT * FROM "{table}" WHERE id = %s', [row_id])  # nosec B608
    row = cursor.fetchone()
    return _reveal(table, dict(row)) if row else None


def _update(cursor, table: str, row_id, values: dict) -> dict | None:
    _check_table(table)
    if not values:
        abort(400, "values must not be empty")
    _check_columns(table, values, WRITABLE)
    values = _protect(table, values)
    assignments = ", ".join(f'"{column}" = %s' for column in values)
    cursor.execute(
        f'UPDATE "{table}" SET {assignments} WHERE id = %s RETURNING *',  # nosec B608
        [_value(value) for value in values.values()] + [row_id],
    )
    row = cursor.fetchone()
    return _reveal(table, dict(row)) if row else None


def _increment(cursor, table: str, row_id, column, by) -> dict | None:
    """Atomic `column = column + by`: concurrent callers never lose an update."""
    _check_table(table)
    if column not in INCREMENTABLE.get(table, set()):
        abort(400, f"'{table}.{column}' cannot be incremented")
    if not isinstance(by, int) or isinstance(by, bool) or abs(by) > MAX_INCREMENT:
        abort(400, f"'by' must be an integer between -{MAX_INCREMENT} and {MAX_INCREMENT}")
    cursor.execute(
        f'UPDATE "{table}" SET "{column}" = "{column}" + %s WHERE id = %s RETURNING *',  # nosec B608
        [by, row_id],
    )
    row = cursor.fetchone()
    return _reveal(table, dict(row)) if row else None


def _delete(cursor, table: str, row_id) -> bool:
    _check_table(table)
    cursor.execute(f'DELETE FROM "{table}" WHERE id = %s', [row_id])  # nosec B608
    return cursor.rowcount > 0


@app.errorhandler(CryptoError)
def crypto_error(exception: CryptoError):
    app.logger.error("field encryption failure: %s", exception)
    return jsonify(error="Internal Server Error", message="stored data could not be decrypted"), 500


@app.errorhandler(psycopg2.Error)
def database_error(exception: psycopg2.Error):
    app.logger.warning("database rejected the operation: %s", exception)
    return jsonify(error="Conflict", message="operation violates a database constraint"), 409


@app.get("/db/<table>")
@require_internal
def select(table: str):
    with connection() as cursor:
        return jsonify(_select(cursor, table, request.args.to_dict()))


@app.get("/db/<table>/count")
@require_internal
def count(table: str):
    with connection() as cursor:
        return jsonify(count=_count(cursor, table, request.args.to_dict()))


@app.get("/db/<table>/<row_id>")
@require_internal
def select_one(table: str, row_id: str):
    with connection() as cursor:
        row = _select_one(cursor, table, row_id)
    if row is None:
        abort(404, f"no row {row_id} in {table}")
    return jsonify(row)


@app.post("/db/<table>")
@require_internal
def insert(table: str):
    with connection() as cursor:
        row = _insert(cursor, table, request.get_json(silent=True) or {})
    return jsonify(row), 201


@app.patch("/db/<table>/<row_id>")
@require_internal
def update(table: str, row_id: str):
    with connection() as cursor:
        row = _update(cursor, table, row_id, request.get_json(silent=True) or {})
    if row is None:
        abort(404, f"no row {row_id} in {table}")
    return jsonify(row)


@app.delete("/db/<table>/<row_id>")
@require_internal
def delete(table: str, row_id: str):
    with connection() as cursor:
        deleted = _delete(cursor, table, row_id)
    if not deleted:
        abort(404, f"no row {row_id} in {table}")
    return "", 204


@app.post("/db/batch")
@require_internal
def batch():
    body = request.get_json(silent=True) or {}
    operations = body.get("operations")
    if not isinstance(operations, list) or not 0 < len(operations) <= 50:
        abort(400, "operations must be a non-empty list of at most 50 entries")

    results = []
    with connection() as cursor:
        for operation in operations:
            if not isinstance(operation, dict):
                abort(400, "each operation must be an object")
            name = operation.get("op")
            table = operation.get("table")
            if name == "insert":
                results.append(_insert(cursor, table, operation.get("values") or {}))
            elif name == "update":
                row = _update(cursor, table, operation.get("id"), operation.get("values") or {})
                if row is None:
                    abort(404, f"no row {operation.get('id')} in {table}")
                results.append(row)
            elif name == "increment":
                row = _increment(
                    cursor, table, operation.get("id"), operation.get("column"), operation.get("by")
                )
                if row is None:
                    abort(404, f"no row {operation.get('id')} in {table}")
                results.append(row)
            elif name == "delete":
                results.append(_delete(cursor, table, operation.get("id")))
            elif name == "select":
                results.append(_select(cursor, table, operation.get("filters") or {}))
            else:
                abort(400, f"unknown op '{name}'")
    return jsonify(results), 201
