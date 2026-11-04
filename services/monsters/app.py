"""monsters: the public catalogue and generated artwork (US-10, US-11)."""

from xml.sax.saxutils import escape

from flask import Response, abort, jsonify, request

from ase_common import DB, create_app

app = create_app("monsters")
db = DB()

ELEMENT_COLOURS = {
    "fire": "#e2571e",
    "water": "#1e7de2",
    "nature": "#2f9e44",
    "air": "#8f7ae0",
    "earth": "#8a6d3b",
}


def _public(monster: dict) -> dict:
    return {
        "id": monster["id"],
        "name": monster["name"],
        "element": monster["element"],
        "hp": monster["hp"],
        "attack": monster["attack"],
        "defence": monster["defence"],
        "rarity": monster["rarity"],
        "description": monster["description"],
        "image_url": f"/monsters/{monster['id']}/image",
    }


@app.get("/monsters")
def catalogue():
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 200))
    except ValueError:
        abort(400, "limit must be an integer")
    return jsonify([_public(monster) for monster in db.select("monsters", order_by="id", limit=limit)])


@app.get("/monsters/<int:monster_id>")
def monster(monster_id: int):
    row = db.get("monsters", monster_id)
    if row is None:
        abort(404, f"monster {monster_id} does not exist")
    return jsonify(_public(row))


@app.get("/monsters/<int:monster_id>/image")
def monster_image(monster_id: int):
    """Every monster needs an image: a deterministic SVG keeps the repo asset-free."""
    row = db.get("monsters", monster_id)
    if row is None:
        abort(404, f"monster {monster_id} does not exist")
    colour = ELEMENT_COLOURS.get(row["element"], "#555555")
    name = escape(row["name"])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
        f'<rect width="256" height="256" rx="24" fill="{colour}"/>'
        '<circle cx="128" cy="112" r="58" fill="#ffffff" fill-opacity="0.9"/>'
        f'<text x="128" y="132" font-family="sans-serif" font-size="48" text-anchor="middle" '
        f'fill="{colour}">{name[0]}</text>'
        '<text x="128" y="212" font-family="sans-serif" font-size="22" text-anchor="middle" '
        f'fill="#ffffff">{name}</text>'
        "</svg>"
    )
    return Response(svg, mimetype="image/svg+xml")
