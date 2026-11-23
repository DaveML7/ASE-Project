"""Checks on everything a caller sends before any SQL is built. Pure: no database access."""

from dataclasses import dataclass
from uuid import UUID

from flask import abort

from .policy import Table

MAX_INCREMENT = 10_000
MAX_IN_VALUES = 200
MAX_LIMIT = 500
FILTER_OPS = {"eq", "gt", "in"}


@dataclass(frozen=True)
class Filter:
    op: str
    column: str
    value: object  # a scalar, or a list of ints for `in`


@dataclass(frozen=True)
class Query:
    filters: tuple
    order_column: str = "id"
    descending: bool = False
    limit: int = 200


def require_object(value, field):
    if not isinstance(value, dict):
        abort(400, f"{field} must be an object")
    return value


def row_id(table: str, value):
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


def check_columns(table_name: str, columns, allowed) -> None:
    unknown = [column for column in columns if column not in allowed]
    if unknown:
        abort(400, f"unknown column(s) for '{table_name}': {', '.join(unknown)}")


def write_values(table_name: str, spec: Table, values, allowed) -> dict:
    require_object(values, "values")
    if not values:
        abort(400, "values must not be empty")
    check_columns(table_name, values, allowed)
    if any(
        isinstance(value, (dict, list))
        for key, value in values.items()
        if (table_name, key) != ("encounters", "details")
    ):
        abort(400, "only encounters.details accepts objects and arrays")
    if "password_hash" in values and not isinstance(values["password_hash"], str):
        abort(400, "password_hash must be a string")
    if "registration_complete" in values and not isinstance(values["registration_complete"], bool):
        abort(400, "registration_complete must be a boolean")
    return values


def _filter(table_name: str, spec: Table, key: str, value) -> Filter:
    op, _, column = key.partition(".")
    if op not in FILTER_OPS:
        abort(400, f"unknown filter '{key}'")
    check_columns(table_name, [column], spec.filterable)
    if op == "in":
        if column not in spec.integer_filters or not isinstance(value, str):
            abort(400, "in filters require an integer column and a comma-separated string")
        try:
            items = [int(item) for item in value.split(",")] if value else []
        except ValueError:
            abort(400, f"in.{column} must be a comma-separated list of integers")
        if len(items) > MAX_IN_VALUES or any(not -(2**63) <= item < 2**63 for item in items):
            abort(400, f"in.{column} supports at most {MAX_IN_VALUES} signed 64-bit integers")
        return Filter("in", column, items)
    if not isinstance(value, (str, int, float)):
        abort(400, f"'{key}' must be a scalar")
    return Filter(op, column, str(value))


def parse_filters(table_name: str, spec: Table, params) -> tuple:
    return tuple(
        _filter(table_name, spec, key, value)
        for key, value in require_object(params, "filters").items()
    )


def parse_query(table_name: str, spec: Table, params) -> Query:
    params = require_object(params, "filters").copy()
    order_by = params.pop("order_by", "id")
    if not isinstance(order_by, str):
        abort(400, "order_by must be a string")
    order_column = order_by.removeprefix("-")
    check_columns(table_name, [order_column], spec.filterable)
    limit = params.pop("limit", 200)
    try:
        if not isinstance(limit, (int, str)) or isinstance(limit, bool):
            raise ValueError
        limit = max(1, min(int(limit), MAX_LIMIT))
    except ValueError:
        abort(400, "limit must be an integer")
    return Query(
        parse_filters(table_name, spec, params), order_column, order_by.startswith("-"), limit
    )


def increment_args(table_name: str, spec: Table, column, by):
    if not isinstance(column, str) or column not in spec.incrementable:
        abort(400, f"'{table_name}.{column}' cannot be incremented")
    if not isinstance(by, int) or isinstance(by, bool) or abs(by) > MAX_INCREMENT:
        abort(400, f"'by' must be an integer between -{MAX_INCREMENT} and {MAX_INCREMENT}")
    return column, by
