"""HTTP routes of the db-manager. Each handler: authorise, validate, run one operation."""

from functools import wraps

from ase_common import json_object, require_service
from flask import Blueprint, abort, g, jsonify, request

from . import dedicated, repository, validation
from .database import transaction
from .policy import CALLERS, TABLES, authorize, table_spec

bp = Blueprint("db", __name__)


def db_endpoint(action: str):
    """Only known services, and only for the table/operation their role allows."""

    def decorator(view):
        @require_service(*CALLERS)
        @wraps(view)
        def wrapper(table, *args, **kwargs):
            authorize(g.caller, table, action)
            return view(table, *args, **kwargs)

        return wrapper

    return decorator


@bp.get("/db/<table>")
@db_endpoint("select")
def select(table: str):
    query = validation.parse_query(table, table_spec(table), request.args.to_dict())
    with transaction(g.caller) as cursor:
        return jsonify(repository.select(cursor, table, query))


@bp.get("/db/<table>/count")
@db_endpoint("select")
def count(table: str):
    filters = validation.parse_filters(table, table_spec(table), request.args.to_dict())
    with transaction(g.caller) as cursor:
        return jsonify(count=repository.count(cursor, table, filters))


@bp.get("/db/<table>/<row_id>")
@db_endpoint("select")
def select_one(table: str, row_id: str):
    row_id = validation.row_id(table, row_id)
    with transaction(g.caller) as cursor:
        row = repository.select_one(cursor, table, row_id)
    if row is None:
        abort(404, f"no row {row_id} in {table}")
    return jsonify(row)


@bp.post("/db/<table>")
@db_endpoint("insert")
def insert(table: str):
    spec = table_spec(table)
    values = validation.write_values(table, spec, request.get_json(silent=True), spec.writable)
    with transaction(g.caller) as cursor:
        row = repository.insert(cursor, table, values)
    return jsonify(row), 201


@bp.patch("/db/<table>/<row_id>")
@db_endpoint("update")
def update(table: str, row_id: str):
    spec = table_spec(table)
    row_id = validation.row_id(table, row_id)
    values = validation.write_values(table, spec, request.get_json(silent=True), spec.updatable)
    with transaction(g.caller) as cursor:
        row = repository.update(cursor, table, row_id, values)
    if row is None:
        abort(404, f"no row {row_id} in {table}")
    return jsonify(row)


@bp.delete("/db/<table>/<row_id>")
@db_endpoint("delete")
def delete(table: str, row_id: str):
    row_id = validation.row_id(table, row_id)
    with transaction(g.caller) as cursor:
        deleted = repository.delete(cursor, table, row_id)
    if not deleted:
        abort(404, f"no row {row_id} in {table}")
    return "", 204


def _team_members(body: dict) -> list[int]:
    members = body.get("member_ids")
    if not isinstance(members, list) or not 1 <= len(members) <= dedicated.MAX_TEAM_SIZE:
        abort(400, "member_ids must contain one to three IDs")
    if not all(type(member) is int for member in members):
        abort(400, "member IDs must be integers")
    members = [validation.row_id("player_monsters", member) for member in members]
    if len(set(members)) != len(members):
        abort(400, "member IDs must be distinct")
    return members


@bp.put("/db/teams/<team_id>/slots")
@require_service("teams")
def replace_team_slots(team_id: str):
    team_id = validation.row_id("teams", team_id)
    body = json_object()
    members = _team_members(body)
    name = body.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 40):
        abort(400, "name must be a string of at most 40 characters")
    with transaction(g.caller) as cursor:
        return jsonify(dedicated.replace_team_slots(cursor, team_id, members, name))


@bp.post("/db/teams/with-slots")
@require_service("teams")
def create_team():
    body = json_object()
    player_id = validation.row_id("players", body.get("player_id"))
    members = _team_members(body)
    name = body.get("name")
    if not isinstance(name, str) or not 0 < len(name) <= 40:
        abort(400, "name must be a string of 1 to 40 characters")
    with transaction(g.caller) as cursor:
        return jsonify(dedicated.create_team(cursor, player_id, name, members)), 201


@bp.delete("/db/revoked_tokens/expired")
@require_service("auth")
def purge_revocations():
    with transaction(g.caller) as cursor:
        return jsonify(deleted=dedicated.purge_expired_revocations(cursor))


@bp.get("/db/encounters/history")
@require_service("history")
def encounter_history():
    authorize(g.caller, "encounters", "select")
    player_id = validation.row_id("players", request.args.get("player_id"))
    before = request.args.get("before")
    before = validation.row_id("encounters", before) if before is not None else None
    try:
        limit = int(request.args.get("limit", 51))
    except ValueError:
        abort(400, "limit must be an integer")
    if not 1 <= limit <= 201:
        abort(400, "limit must be between 1 and 201")
    with transaction(g.caller) as cursor:
        return jsonify(dedicated.encounter_history(cursor, player_id, before, limit))


@bp.post("/db/batch")
@require_service(*CALLERS)
def batch():
    """Up to 50 operations in ONE transaction: validated and authorised first, run second."""
    body = validation.require_object(request.get_json(silent=True), "body")
    operations = body.get("operations")
    if not isinstance(operations, list) or not 0 < len(operations) <= 50:
        abort(400, "operations must be a non-empty list of at most 50 entries")

    plan = []
    for operation in operations:
        validation.require_object(operation, "operation")
        name, table = operation.get("op"), operation.get("table")
        if not isinstance(name, str) or name not in HANDLERS or not isinstance(table, str):
            abort(400, "operation requires a supported op and a table name")
        authorize(g.caller, table, name)
        plan.append((name, table, _arguments(name, table, operation)))

    results = []
    with transaction(g.caller) as cursor:
        for name, table, args in plan:
            result = HANDLERS[name](cursor, table, *args)
            if result is None:
                abort(404, f"no row {args[0]} in {table}")
            results.append(result)
    return jsonify(results), 201


HANDLERS = {
    "insert": repository.insert,
    "update": repository.update,
    "increment": repository.increment,
    "delete": repository.delete,
    "select": repository.select,
}


def _arguments(name: str, table: str, operation: dict) -> tuple:
    spec = TABLES[table]
    if name == "insert":
        return (validation.write_values(table, spec, operation.get("values"), spec.writable),)
    if name == "select":
        return (validation.parse_query(table, spec, operation.get("filters", {})),)
    args = (validation.row_id(table, operation.get("id")),)
    if name == "update":
        return args + (
            validation.write_values(table, spec, operation.get("values"), spec.updatable),
        )
    if name == "increment":
        return args + validation.increment_args(
            table, spec, operation.get("column"), operation.get("by")
        )
    return args
