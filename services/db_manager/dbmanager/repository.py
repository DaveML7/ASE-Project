"""Runs the built SQL on a cursor and applies encryption. One small function per operation."""

from psycopg2.extras import Json

from . import sql as q
from .policy import TABLES
from .protection import protect, reveal
from .validation import Query


def _value(value):
    return Json(value) if isinstance(value, (dict, list)) else value


def insert(cursor, table: str, values: dict) -> dict:
    values = protect(table, values)
    cursor.execute(q.insert(table, list(values)), [_value(v) for v in values.values()])
    return reveal(table, dict(cursor.fetchone()))


def select(cursor, table: str, query: Query) -> list[dict]:
    text, params = q.select(table, query)
    cursor.execute(text, params)
    return [reveal(table, dict(row)) for row in cursor.fetchall()]


def count(cursor, table: str, filters) -> int:
    text, params = q.count(table, filters)
    cursor.execute(text, params)
    return cursor.fetchone()["n"]


def select_one(cursor, table: str, row_id) -> dict | None:
    cursor.execute(q.select_one(table), [row_id])
    row = cursor.fetchone()
    return reveal(table, dict(row)) if row else None


def update(cursor, table: str, row_id, values: dict) -> dict | None:
    cursor.execute(q.update(table, list(values)), [*(_value(v) for v in values.values()), row_id])
    row = cursor.fetchone()
    return reveal(table, dict(row)) if row else None


def increment(cursor, table: str, row_id, column: str, by: int) -> dict | None:
    returned = TABLES[table].returned_on_increment
    cursor.execute(q.increment(table, column, returned), [by, row_id])
    row = cursor.fetchone()
    return dict(row) if row else None


def delete(cursor, table: str, row_id) -> bool:
    cursor.execute(q.delete(table), [row_id])
    return cursor.rowcount > 0
