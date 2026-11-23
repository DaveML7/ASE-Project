"""Builds the SQL text. Pure functions: (sql, parameters) in, nothing executed here.

Identifiers (schema, table, column) are only ever composed with `psycopg2.sql.Identifier`
after the policy and validation modules accepted them; every value is a bound parameter.
"""

from psycopg2 import sql

from .policy import TABLES
from .validation import Query


def table_ref(table: str) -> sql.Identifier:
    return sql.Identifier(TABLES[table].schema, table)


def _columns(columns) -> sql.Composable:
    return sql.SQL(", ").join(sql.Identifier(column) for column in columns)


def _where(filters) -> tuple[sql.Composable, list]:
    if not filters:
        return sql.SQL(""), []
    parts, params = [], []
    for item in filters:
        column = sql.Identifier(item.column)
        if item.op == "in":
            parts.append(sql.SQL("{} = ANY(%s)").format(column))
        else:
            parts.append(
                sql.SQL("{} {} %s").format(column, sql.SQL("=" if item.op == "eq" else ">"))
            )
        params.append(item.value)
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts), params


def insert(table: str, columns) -> sql.Composable:
    return sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
        table_ref(table), _columns(columns), sql.SQL(", ").join(sql.Placeholder() * len(columns))
    )


def select(table: str, query: Query) -> tuple[sql.Composable, list]:
    where, params = _where(query.filters)
    text = sql.SQL("SELECT * FROM {}{} ORDER BY {} {} LIMIT %s").format(
        table_ref(table),
        where,
        sql.Identifier(query.order_column),
        sql.SQL("DESC" if query.descending else "ASC"),
    )
    return text, [*params, query.limit]


def count(table: str, filters) -> tuple[sql.Composable, list]:
    where, params = _where(filters)
    return sql.SQL("SELECT count(*) AS n FROM {}{}").format(table_ref(table), where), params


def select_one(table: str) -> sql.Composable:
    return sql.SQL("SELECT * FROM {} WHERE id = %s").format(table_ref(table))


def update(table: str, columns) -> sql.Composable:
    assignments = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in columns)
    return sql.SQL("UPDATE {} SET {} WHERE id = %s RETURNING *").format(
        table_ref(table), assignments
    )


def increment(table: str, column: str, returned) -> sql.Composable:
    return sql.SQL("UPDATE {t} SET {c} = {c} + %s WHERE id = %s RETURNING {r}").format(
        t=table_ref(table), c=sql.Identifier(column), r=_columns(returned)
    )


def delete(table: str) -> sql.Composable:
    return sql.SQL("DELETE FROM {} WHERE id = %s").format(table_ref(table))
