"""teams: create, modify and validate teams (US-14, US-15, US-16). Routes only: the data access
is in `team_store` and the rules in `team_rules`."""

from ase_common import (
    DB,
    DbError,
    create_app,
    error,
    owned_resource,
    require_jwt,
    require_service,
    with_json_body,
)
from flask import g, jsonify
from team_rules import TEAM_MAX_SIZE, TEAMS_PER_PLAYER, validate_team
from team_store import TeamStore, member_ids_from, name_from, owned_monster_ids

app = create_app("teams")
db = DB()


store = TeamStore(lambda: db)
my_team = owned_resource(store.load, not_found="team not found", key="team")


def _checked_members(body: dict):
    """(member_ids, None) for a valid team of the caller, else (None, error response)."""
    member_ids = member_ids_from(body)
    if member_ids is None:
        return None, error(400, "member_ids must be a list of integers")
    problems = validate_team(member_ids, owned_monster_ids(g.player_id))
    if problems:
        return None, error(422, "team is not valid", violations=problems)
    return member_ids, None


@app.get("/teams")
@require_jwt
def list_teams():
    return jsonify(store.payloads(store.of_player(g.player_id)))


@app.post("/teams")
@require_jwt
@with_json_body
def create_team(body):
    member_ids, failure = _checked_members(body)
    if failure:
        return failure
    name, valid = name_from(body, "Team")
    if not valid:
        return error(400, "name must be a non-empty string of at most 40 characters")
    try:
        payload = db.create_team(g.player_id, name, member_ids)
    except DbError as exc:
        if exc.conflicts_with("teams_per_player_limit"):
            return error(409, f"you already have {TEAMS_PER_PLAYER} teams: delete one first")
        raise
    return jsonify(**payload, max_size=TEAM_MAX_SIZE), 201


@app.get("/teams/<id:team_id>")
@require_jwt
@my_team
def get_team(team):
    return jsonify(store.payload(team))


@app.put("/teams/<id:team_id>")
@require_jwt
@my_team
@with_json_body
def update_team(team, body):
    member_ids, failure = _checked_members(body)
    if failure:
        return failure
    name, valid = name_from(body, None)
    if not valid:
        return error(400, "name must be a non-empty string of at most 40 characters")
    payload = db.replace_team_slots(team["id"], member_ids, name)
    return jsonify(**payload, max_size=TEAM_MAX_SIZE)


@app.delete("/teams/<id:team_id>")
@require_jwt
@my_team
def delete_team(team):
    db.delete("teams", team["id"])  # team_slots cascade
    return "", 204


@app.post("/teams/<id:team_id>/validate")
@require_jwt
@my_team
def validate_existing_team(team):
    member_ids = store.payload(team)["member_ids"]
    problems = validate_team(member_ids, owned_monster_ids(g.player_id))
    return jsonify(valid=not problems, violations=problems, member_ids=member_ids)


@app.get("/internal/teams/<id:team_id>")
@require_service("encounters")
@require_jwt
def internal_team(team_id: int):
    team = store.load(team_id)
    if team is None:
        return error(404, "team not found")
    if str(team["player_id"]) != str(g.player_id):  # the caller must hold the owner's token
        return error(403, "that team belongs to another player")
    return jsonify(store.payload(team) | {"player_id": str(team["player_id"])})
