"""db-manager: the only service allowed to touch PostgreSQL.

Exposes generic CRUD plus a transactional batch endpoint. Callers never send
SQL: table names and columns are checked against the allowlists below.
"""

from contextlib import contextmanager

import psycopg2
from flask import abort, jsonify, request
from psycopg2 import pool
from psycopg2.extras import Json, RealDictCursor

from ase_common import create_app, require_internal

app = create_app("db-manager")

# Columns each service may write. Anything else is rejected with 400.
WRITABLE = {
    "players": {"id", "username", "avatar", "faction", "level", "xp"},
    "credentials": {"player_id", "username", "password_hash"},
    "monsters": {"name", "element", "hp", "attack", "defence", "rarity", "description"},
    "player_monsters": {"player_id", "monster_id", "nickname", "level", "source"},
    "teams": {"player_id", "name"},
    "team_slots": {"team_id", "slot", "player_monster_id"},
    "encounter_templates": {"name", "wild_monster_id", "difficulty", "description"},
    "encounters": {"player_id", "team_id", "template_id", "wild_monster_id", "outcome", "details"},
}
FILTERABLE = {
    table: columns | {"id", "created_at", "acquired_at"} for table, columns in WRITABLE.items()
}

_connection_pool: pool.SimpleConnectionPool | None = None


def get_pool() -> pool.SimpleConnectionPool:
    global _connection_pool
    if _connection_pool is None:
        # libpq reads PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD from the env.
        _connection_pool = pool.SimpleConnectionPool(1, 10)
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


def _check_table(table: str) -> None:
    if table not in WRITABLE:
        abort(404, f"unknown table '{table}'")


def _check_columns(table: str, columns, allowed: dict) -> None:
    unknown = [column for column in columns if column not in allowed[table]]
    if unknown:
        abort(400, f"unknown column(s) for '{table}': {', '.join(unknown)}")


def _value(value):
    return Json(value) if isinstance(value, (dict, list)) else value


def _insert(cursor, table: str, values: dict) -> dict:
    _check_table(table)
    if not values:
        abort(400, "values must not be empty")
    _check_columns(table, values, WRITABLE)
    columns = list(values)
    placeholders = ", ".join(["%s"] * len(columns))
    cursor.execute(
        f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({placeholders}) RETURNING *',
        [_value(values[column]) for column in columns],
    )
    return dict(cursor.fetchone())


def _select(cursor, table: str, params: dict) -> list[dict]:
    _check_table(table)
    where, arguments = [], []
    for key, value in params.items():
        if not key.startswith("eq."):
            continue
        column = key.removeprefix("eq.")
        _check_columns(table, [column], FILTERABLE)
        where.append(f'"{column}" = %s')
        arguments.append(value)

    order_by = str(params.get("order_by", "id"))
    descending = order_by.startswith("-")
    order_column = order_by.lstrip("-")
    _check_columns(table, [order_column], FILTERABLE)
    try:
        limit = max(1, min(int(params.get("limit", 200)), 500))
    except ValueError:
        abort(400, "limit must be an integer")

    sql = f'SELECT * FROM "{table}"'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f' ORDER BY "{order_column}" {"DESC" if descending else "ASC"} LIMIT %s'
    cursor.execute(sql, [*arguments, limit])
    return [dict(row) for row in cursor.fetchall()]


def _select_one(cursor, table: str, row_id) -> dict | None:
    _check_table(table)
    cursor.execute(f'SELECT * FROM "{table}" WHERE id = %s', [row_id])
    row = cursor.fetchone()
    return dict(row) if row else None


def _update(cursor, table: str, row_id, values: dict) -> dict | None:
    _check_table(table)
    if not values:
        abort(400, "values must not be empty")
    _check_columns(table, values, WRITABLE)
    assignments = ", ".join(f'"{column}" = %s' for column in values)
    cursor.execute(
        f'UPDATE "{table}" SET {assignments} WHERE id = %s RETURNING *',
        [_value(value) for value in values.values()] + [row_id],
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _delete(cursor, table: str, row_id) -> bool:
    _check_table(table)
    cursor.execute(f'DELETE FROM "{table}" WHERE id = %s', [row_id])
    return cursor.rowcount > 0


@app.errorhandler(psycopg2.Error)
def database_error(exception: psycopg2.Error):
    app.logger.warning("database rejected the operation: %s", exception)
    return jsonify(error="Conflict", message="operation violates a database constraint"), 409


@app.get("/db/<table>")
@require_internal
def select(table: str):
    with connection() as cursor:
        return jsonify(_select(cursor, table, request.args.to_dict()))


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
            elif name == "delete":
                results.append(_delete(cursor, table, operation.get("id")))
            elif name == "select":
                results.append(_select(cursor, table, operation.get("filters") or {}))
            else:
                abort(400, f"unknown op '{name}'")
    return jsonify(results), 201
