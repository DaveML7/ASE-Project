"""players: profile, saved progress and the personal collection (US-03, US-06, US-09)."""

from flask import g, jsonify, request

from ase_common import DB, create_app, require_internal, require_jwt

app = create_app("players")
db = DB()

STARTER_MONSTERS = 3


def _collection(player_id: str) -> list[dict]:
    """Player monsters joined with the catalogue. Two db-manager reads, stitched here.

    shortcut: both reads are capped at 500 rows, fix with a join endpoint in
    db-manager if the catalogue or a collection can grow past that.
    """
    owned = db.select("player_monsters", player_id=player_id, order_by="id", limit=500)
    if not owned:
        return []
    catalogue = {monster["id"]: monster for monster in db.select("monsters", limit=500)}
    collection = []
    for entry in owned:
        monster = catalogue.get(entry["monster_id"])
        if monster is None:
            continue
        collection.append(
            {
                "player_monster_id": entry["id"],
                "monster_id": monster["id"],
                "name": monster["name"],
                "element": monster["element"],
                "hp": monster["hp"],
                "attack": monster["attack"],
                "defence": monster["defence"],
                "rarity": monster["rarity"],
                "description": monster["description"],
                "nickname": entry["nickname"],
                "level": entry["level"],
                "image_url": f"/monsters/{monster['id']}/image",
            }
        )
    return collection


@app.post("/internal/players")
@require_internal
def create_player():
    body = request.get_json(silent=True) or {}
    player_id = str(body.get("player_id") or "")
    username = str(body.get("username") or "").strip()
    if not player_id or not username:
        return {"error": "Bad Request", "message": "player_id and username are required"}, 400
    if db.get("players", player_id):
        return {"error": "Conflict", "message": "player already exists"}, 409

    starters = db.select("monsters", order_by="id", limit=STARTER_MONSTERS)
    operations = [
        {
            "op": "insert",
            "table": "players",
            "values": {"id": player_id, "username": username},
        }
    ]
    operations += [
        {
            "op": "insert",
            "table": "player_monsters",
            "values": {"player_id": player_id, "monster_id": monster["id"], "source": "starter"},
        }
        for monster in starters
    ]
    db.batch(operations)
    return {
        "player_id": player_id,
        "username": username,
        "starter_monsters": [monster["id"] for monster in starters],
    }, 201


@app.get("/internal/players/<player_id>/monsters")
@require_internal
def internal_collection(player_id: str):
    return jsonify(_collection(player_id))


@app.get("/players/me")
@require_jwt
def profile():
    player = db.get("players", g.player_id)
    if player is None:
        return {"error": "Not Found", "message": "player profile not found"}, 404
    return jsonify(
        player_id=player["id"],
        username=player["username"],
        avatar=player["avatar"],
        faction=player["faction"],
        level=player["level"],
        xp=player["xp"],
        monsters_owned=len(db.select("player_monsters", player_id=g.player_id, limit=500)),
    )


@app.patch("/players/me")
@require_jwt
def update_profile():
    body = request.get_json(silent=True) or {}
    changes = {}
    if "avatar" in body:
        avatar = str(body["avatar"]).strip()
        if not 1 <= len(avatar) <= 40:
            return {"error": "Bad Request", "message": "avatar must be 1-40 characters"}, 400
        changes["avatar"] = avatar
    if "faction" in body:
        faction = str(body["faction"]).strip()
        if len(faction) > 40:
            return {"error": "Bad Request", "message": "faction must be at most 40 characters"}, 400
        changes["faction"] = faction or None
    if not changes:
        return {"error": "Bad Request", "message": "nothing to update"}, 400
    player = db.update("players", g.player_id, changes)
    if player is None:
        return {"error": "Not Found", "message": "player profile not found"}, 404
    return jsonify(player_id=player["id"], username=player["username"], avatar=player["avatar"],
                   faction=player["faction"], level=player["level"], xp=player["xp"])


@app.get("/players/me/monsters")
@require_jwt
def collection():
    return jsonify(_collection(g.player_id))
