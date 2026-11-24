"""history: read-only view of a player's past encounters (US-23, US-24)."""

from ase_common import DB, create_app, error, is_row_id, parse_limit, require_jwt
from flask import g, jsonify, request

app = create_app("history")
db = DB()


def _object(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) else None


def _public(encounter: dict, details: dict) -> dict:
    team = details.get("team")
    names = (
        [_text(_object(member).get("name")) for member in team] if isinstance(team, list) else []
    )
    return {
        "id": encounter["id"],
        "template_id": encounter["template_id"],
        "wild_monster_id": encounter["wild_monster_id"],
        "outcome": encounter["outcome"],
        "summary": {
            "template": _text(details.get("template")),
            "wild_monster": _text(_object(details.get("wild_monster")).get("name")),
            "team": [name for name in names if name is not None],
            "reward": _text(_object(details.get("reward")).get("name")),
        },
        "created_at": encounter["created_at"],
    }


def _cursor(value: str) -> int | None:
    """The `before` query parameter as an encounter id, or None if it cannot be one."""
    if value.isascii() and value.isdigit() and len(value) <= 19 and is_row_id(int(value)):
        return int(value)
    return None


@app.get("/history")
@require_jwt
def list_history():
    limit = parse_limit(default=50, maximum=200)
    before = request.args.get("before")
    cursor = _cursor(before) if before is not None else None
    if before is not None and cursor is None:
        return error(400, "before must be a positive encounter ID")

    encounters = db.encounter_history(g.player_id, limit + 1, cursor)
    response = jsonify([_public(row, _object(row.get("details"))) for row in encounters[:limit]])
    if len(encounters) > limit:
        response.headers["Link"] = (
            f'<?limit={limit}&before={encounters[limit - 1]["id"]}>; rel="next"'
        )
    return response


@app.get("/history/<id:encounter_id>")
@require_jwt
def history_detail(encounter_id: int):
    encounters = db.select("encounters", player_id=g.player_id, id=encounter_id, limit=1)
    if not encounters:
        return error(404, "encounter not found")
    encounter = encounters[0]
    details = _object(encounter.get("details"))
    return jsonify({**_public(encounter, details), "details": details})
