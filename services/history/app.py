"""history: read-only view of a player's past encounters (US-23, US-24)."""

from flask import g, jsonify, request

from ase_common import DB, create_app, require_jwt

app = create_app("history")
db = DB()


def _public(encounter: dict) -> dict:
    return {
        "id": encounter["id"],
        "template_id": encounter["template_id"],
        "wild_monster_id": encounter["wild_monster_id"],
        "outcome": encounter["outcome"],
        "summary": {
            "template": encounter["details"].get("template"),
            "wild_monster": (encounter["details"].get("wild_monster") or {}).get("name"),
            "team": [member.get("name") for member in encounter["details"].get("team", [])],
            "reward": (encounter["details"].get("reward") or {}).get("name"),
        },
        "created_at": encounter.get("created_at"),
    }


@app.get("/history")
@require_jwt
def list_history():
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except ValueError:
        return {"error": "Bad Request", "message": "limit must be an integer"}, 400
    encounters = db.select(
        "encounters", player_id=g.player_id, order_by="-created_at", limit=limit
    )
    return jsonify(
        [
            {**_public(encounter), "outcome": encounter["outcome"]}
            for encounter in encounters
        ]
    )


@app.get("/history/<int:encounter_id>")
@require_jwt
def history_detail(encounter_id: int):
    encounter = db.get("encounters", encounter_id)
    if encounter is None or str(encounter["player_id"]) != str(g.player_id):
        return {"error": "Not Found", "message": "encounter not found"}, 404
    return jsonify({**_public(encounter), "details": encounter["details"]})
