"""encounters: browse, start and resolve encounters, award rewards (US-17..US-20)."""

from flask import g, jsonify, request

from ase_common import DB, call_service, create_app, require_jwt

from encounter_rules import pick_reward, resolve

app = create_app("encounters")
db = DB()

XP_WIN = 20
XP_LOSS = 5
XP_PER_LEVEL = 100


def _public(encounter: dict) -> dict:
    return {
        "id": encounter["id"],
        "template_id": encounter["template_id"],
        "team_id": encounter["team_id"],
        "wild_monster_id": encounter["wild_monster_id"],
        "outcome": encounter["outcome"],
        "details": encounter["details"],
        "created_at": encounter.get("created_at"),
    }


def _templates() -> list[dict]:
    templates = db.select("encounter_templates", order_by="id", limit=50)
    catalogue = {monster["id"]: monster for monster in db.select("monsters", limit=500)}
    return [
        {
            "id": template["id"],
            "name": template["name"],
            "difficulty": template["difficulty"],
            "description": template["description"],
            "wild_monster": _monster_summary(catalogue.get(template["wild_monster_id"])),
        }
        for template in templates
    ]


def _monster_summary(monster: dict | None) -> dict | None:
    if monster is None:
        return None
    return {
        "id": monster["id"],
        "name": monster["name"],
        "element": monster["element"],
        "hp": monster["hp"],
        "attack": monster["attack"],
        "defence": monster["defence"],
        "rarity": monster["rarity"],
        "image_url": f"/monsters/{monster['id']}/image",
    }


@app.get("/encounters")
@require_jwt
def available_encounters():
    return jsonify(_templates())


@app.post("/encounters")
@require_jwt
def start_encounter():
    body = request.get_json(silent=True) or {}
    team_id, template_id = body.get("team_id"), body.get("template_id")
    if not isinstance(team_id, int) or not isinstance(template_id, int):
        return {"error": "Bad Request", "message": "team_id and template_id must be integers"}, 400

    # The team and the collection are owned by other services: ask them.
    team_response = call_service("teams", "GET", f"/internal/teams/{team_id}")
    if team_response.status_code == 404:
        return {"error": "Not Found", "message": "team not found"}, 404
    team_response.raise_for_status()
    team = team_response.json()
    if team["player_id"] != str(g.player_id):
        return {"error": "Forbidden", "message": "that team belongs to another player"}, 403

    template = db.get("encounter_templates", template_id)
    if template is None:
        return {"error": "Not Found", "message": "encounter does not exist"}, 404
    wild = db.get("monsters", template["wild_monster_id"])

    collection_response = call_service(
        "players", "GET", f"/internal/players/{g.player_id}/monsters"
    )
    if collection_response.status_code != 200:
        return {"error": "Bad Gateway", "message": "could not read your collection"}, 502
    collection = collection_response.json()
    by_player_monster = {entry["player_monster_id"]: entry for entry in collection}
    members = [
        by_player_monster[member_id]
        for member_id in team["member_ids"]
        if member_id in by_player_monster
    ]
    if not members:
        return {"error": "Unprocessable Entity", "message": "team has no usable monsters"}, 422

    outcome = resolve(members, wild, template["difficulty"])
    player = db.get("players", g.player_id)
    if player is None:
        return {"error": "Not Found", "message": "player profile not found"}, 404

    xp = player["xp"] + (XP_WIN if outcome["success"] else XP_LOSS)
    details = {
        "template": template["name"],
        "difficulty": template["difficulty"],
        "wild_monster": _monster_summary(wild),
        "team": [
            {
                "player_monster_id": member["player_monster_id"],
                "monster_id": member["monster_id"],
                "name": member["name"],
                "element": member["element"],
                "level": member["level"],
                "attack": member["attack"],
                "defence": member["defence"],
                "hp": member["hp"],
            }
            for member in members
        ],
        "resolution": outcome,
    }

    catalogue = db.select("monsters", order_by="id", limit=500)
    owned_monster_ids = {entry["monster_id"] for entry in collection}
    reward = None
    if outcome["success"]:
        reward = pick_reward([m for m in catalogue if m["id"] not in owned_monster_ids])
        if reward is not None:
            details["reward"] = _monster_summary(reward)

    # Encounter, reward and progress land in one transaction: no partial results.
    operations = [
        {
            "op": "insert",
            "table": "encounters",
            "values": {
                "player_id": g.player_id,
                "team_id": team_id,
                "template_id": template_id,
                "wild_monster_id": wild["id"],
                "outcome": "win" if outcome["success"] else "loss",
                "details": details,
            },
        }
    ]
    if reward is not None:
        operations.append(
            {
                "op": "insert",
                "table": "player_monsters",
                "values": {
                    "player_id": g.player_id,
                    "monster_id": reward["id"],
                    "source": "reward",
                },
            }
        )
    operations.append(
        {
            "op": "update",
            "table": "players",
            "id": g.player_id,
            "values": {"xp": xp, "level": 1 + xp // XP_PER_LEVEL},
        }
    )
    encounter = db.batch(operations)[0]
    return jsonify(_public(encounter)), 201


@app.get("/encounters/<int:encounter_id>")
@require_jwt
def encounter(encounter_id: int):
    row = db.get("encounters", encounter_id)
    if row is None or str(row["player_id"]) != str(g.player_id):
        return {"error": "Not Found", "message": "encounter not found"}, 404
    return jsonify(_public(row))
