"""monsters: the public catalogue and generated artwork (US-10, US-11).

`escape` (B406) is only used to escape text written into the SVG output; no XML
is ever parsed.
"""

from xml.sax.saxutils import escape  # nosec B406

from ase_common import DB, create_app, monster_public
from flask import Response, abort, jsonify, request

app = create_app("monsters", required_keys=("service",))  # public routes only: no user-token key
db = DB()

MAX_LIMIT = 200
IMAGE_CACHE = "public, max-age=3600"  # artwork never changes for a given monster

ELEMENT_COLOURS = {
    "fire": "#e2571e",
    "water": "#1e7de2",
    "nature": "#2f9e44",
    "air": "#8f7ae0",
    "earth": "#8a6d3b",
}


def _find(monster_id: int) -> dict:
    """Look a monster up in the cached catalogue (no database round trip per request)."""
    row = next((m for m in db.catalogue() if m["id"] == monster_id), None)
    if row is None:
        abort(404, f"monster {monster_id} does not exist")
    return row


@app.get("/monsters")
def catalogue():
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        abort(400, "limit must be an integer")
    if limit < 1:
        abort(400, "limit must be at least 1")
    limit = min(limit, MAX_LIMIT)
    return jsonify([monster_public(monster) for monster in db.catalogue()[:limit]])


@app.get("/monsters/<int:monster_id>")
def monster(monster_id: int):
    return jsonify(monster_public(_find(monster_id)))


@app.get("/monsters/<int:monster_id>/image")
def monster_image(monster_id: int):
    """Every monster needs an image: a deterministic SVG keeps the repo asset-free."""
    row = _find(monster_id)
    colour = ELEMENT_COLOURS.get(row["element"], "#555555")
    name = escape(row["name"])
    initial = name[:1] or "?"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
        f'<rect width="256" height="256" rx="24" fill="{colour}"/>'
        '<circle cx="128" cy="112" r="58" fill="#ffffff" fill-opacity="0.9"/>'
        f'<text x="128" y="132" font-family="sans-serif" font-size="48" text-anchor="middle" '
        f'fill="{colour}">{initial}</text>'
        '<text x="128" y="212" font-family="sans-serif" font-size="22" text-anchor="middle" '
        f'fill="#ffffff">{name}</text>'
        "</svg>"
    )
    response = Response(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = IMAGE_CACHE
    return response
