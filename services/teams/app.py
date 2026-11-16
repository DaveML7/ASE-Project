"""teams: create, modify and validate teams (US-14, US-15, US-16)."""

from ase_common import DB, bearer_token, call_service, create_app, error, require_jwt, require_service
from flask import g, jsonify, request
from team_rules import TEAM_MAX_SIZE, validate_team

app = create_app("teams")
db = DB()


def _owned_monster_ids(player_id: str) -> set[int]:
    response = call_service(
        "players", "GET", f"/internal/players/{player_id}/monsters", user_token=bearer_token()
    )
    response.raise_for_status()
    return {entry["player_monster_id"] for entry in response.json()}


def _team_payloads(teams: list[dict]) -> list[dict]:
    """Payloads for several teams with ONE slot query (no N+1)."""
    if not teams:
        return []
    slots = db.select(
        "team_slots", team_id__in=[team["id"] for team in teams], order_by="slot", limit=500
    )
    members: dict[int, list[int]] = {team["id"]: [] for team in teams}
    for slot in slots:
        members[slot["team_id"]].append(slot["player_monster_id"])
    return [
        {"id": team["id"], "name": team["name"], "member_ids": members[team["id"]], "max_size": TEAM_MAX_SIZE}
        for team in teams
    ]


def _team_payload(team: dict) -> dict:
    return _team_payloads([team])[0]


def _owned_team(team_id: int, player_id: str) -> dict | None:
    team = db.get("teams", team_id)
    if team is None or str(team["player_id"]) != str(player_id):
        return None
    return team


def _member_ids_from_request() -> list[int] | None:
    body = request.get_json(silent=True) or {}
    member_ids = body.get("member_ids")
    if not isinstance(member_ids, list) or not all(
        isinstance(member, int) and not isinstance(member, bool) for member in member_ids
    ):
        return None
    return member_ids


def _replace_slots(team_id: int, member_ids: list[int]) -> None:
    existing = db.select("team_slots", team_id=team_id, limit=TEAM_MAX_SIZE)
    operations = [
        {"op": "delete", "table": "team_slots", "id": slot["id"]} for slot in existing
    ]
    operations += [
        {
            "op": "insert",
            "table": "team_slots",
            "values": {"team_id": team_id, "slot": index, "player_monster_id": member_id},
        }
        for index, member_id in enumerate(member_ids, start=1)
    ]
    db.batch(operations)


@app.get("/teams")
@require_jwt
def list_teams():
    teams = db.select("teams", player_id=g.player_id, order_by="id", limit=20)
    return jsonify(_team_payloads(teams))


@app.post("/teams")
@require_jwt
def create_team():
    member_ids = _member_ids_from_request()
    if member_ids is None:
        return error(400, "member_ids must be a list of integers")
    problems = validate_team(member_ids, _owned_monster_ids(g.player_id))
    if problems:
        return error(422, "team is not valid", violations=problems)

    name = str((request.get_json(silent=True) or {}).get("name") or "Team").strip()[:40]
    team = db.insert("teams", {"player_id": g.player_id, "name": name})
    try:
        _replace_slots(team["id"], member_ids)
    except Exception:
        db.delete("teams", team["id"])  # never leave a team without its members
        raise
    return jsonify(_team_payload(team)), 201


@app.get("/teams/<int:team_id>")
@require_jwt
def get_team(team_id: int):
    team = _owned_team(team_id, g.player_id)
    if team is None:
        return error(404, "team not found")
    return jsonify(_team_payload(team))


@app.put("/teams/<int:team_id>")
@require_jwt
def update_team(team_id: int):
    team = _owned_team(team_id, g.player_id)
    if team is None:
        return error(404, "team not found")
    member_ids = _member_ids_from_request()
    if member_ids is None:
        return error(400, "member_ids must be a list of integers")
    problems = validate_team(member_ids, _owned_monster_ids(g.player_id))
    if problems:
        return error(422, "team is not valid", violations=problems)

    body = request.get_json(silent=True) or {}
    if body.get("name"):
        team = db.update("teams", team_id, {"name": str(body["name"]).strip()[:40]})
    _replace_slots(team_id, member_ids)
    return jsonify(_team_payload(team))


@app.delete("/teams/<int:team_id>")
@require_jwt
def delete_team(team_id: int):
    team = _owned_team(team_id, g.player_id)
    if team is None:
        return error(404, "team not found")
    db.delete("teams", team_id)  # team_slots cascade
    return "", 204


@app.post("/teams/<int:team_id>/validate")
@require_jwt
def validate_existing_team(team_id: int):
    team = _owned_team(team_id, g.player_id)
    if team is None:
        return error(404, "team not found")
    member_ids = _team_payload(team)["member_ids"]
    problems = validate_team(member_ids, _owned_monster_ids(g.player_id))
    return jsonify(valid=not problems, violations=problems, member_ids=member_ids)


@app.get("/internal/teams/<int:team_id>")
@require_service("encounters")
@require_jwt
def internal_team(team_id: int):
    team = db.get("teams", team_id)
    if team is None:
        return error(404, "team not found")
    if str(team["player_id"]) != str(g.player_id):  # the caller must hold the owner's token
        return error(403, "that team belongs to another player")
    payload = _team_payload(team)
    payload["player_id"] = str(team["player_id"])
    return jsonify(payload)
