"""players: profile, saved progress and the personal collection (US-03, US-06, US-09)."""

from ase_common import DB, collection_entry, create_app, error, require_jwt, require_service
from flask import g, jsonify, request

app = create_app("players")
db = DB()

STARTER_MONSTERS = 3


def _collection(player_id: str) -> list[dict]:
    """Player monsters joined with the (cached) catalogue."""
    owned = db.select("player_monsters", player_id=player_id, order_by="id", limit=500)
    if not owned:
        return []
    catalogue = {monster["id"]: monster for monster in db.catalogue()}
    return [
        collection_entry(entry, catalogue[entry["monster_id"]])
        for entry in owned
        if entry["monster_id"] in catalogue
    ]


@app.post("/internal/players")
@require_service("auth")
def create_player():
    body = request.get_json(silent=True) or {}
    player_id = str(body.get("player_id") or "")
    username = str(body.get("username") or "").strip()
    if not player_id or not username:
        return error(400, "player_id and username are required")
    if db.get("players", player_id):
        return error(409, "player already exists")

    starters = db.catalogue()[:STARTER_MONSTERS]
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
@require_service("teams", "encounters")
@require_jwt
def internal_collection(player_id: str):
    # The caller must hold the OWNER's own token: a service cannot read anyone else's data.
    if g.player_id != player_id:
        return error(403, "the token does not belong to that player")
    return jsonify(_collection(player_id))


@app.get("/players/me")
@require_jwt
def profile():
    player = db.get("players", g.player_id)
    if player is None:
        return error(404, "player profile not found")
    return jsonify(
        player_id=player["id"],
        username=player["username"],
        avatar=player["avatar"],
        faction=player["faction"],
        level=player["level"],
        xp=player["xp"],
        monsters_owned=db.count("player_monsters", player_id=g.player_id),
    )


@app.patch("/players/me")
@require_jwt
def update_profile():
    body = request.get_json(silent=True) or {}
    changes = {}
    if "avatar" in body:
        avatar = str(body["avatar"]).strip()
        if not 1 <= len(avatar) <= 40:
            return error(400, "avatar must be 1-40 characters")
        changes["avatar"] = avatar
    if "faction" in body:
        faction = str(body["faction"]).strip()
        if len(faction) > 40:
            return error(400, "faction must be at most 40 characters")
        changes["faction"] = faction or None
    if not changes:
        return error(400, "nothing to update")
    player = db.update("players", g.player_id, changes)
    if player is None:
        return error(404, "player profile not found")
    return jsonify(player_id=player["id"], username=player["username"], avatar=player["avatar"],
                   faction=player["faction"], level=player["level"], xp=player["xp"])


@app.get("/players/me/monsters")
@require_jwt
def collection():
    return jsonify(_collection(g.player_id))
