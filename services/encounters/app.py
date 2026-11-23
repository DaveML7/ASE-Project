"""encounters: browse, start and read encounters (US-17..US-20). Routes only; the work is in
`encounter_flow` and the rules in `encounter_rules`."""

from ase_common import (
    DB,
    DbError,
    bearer_token,
    cacheable_json,
    call_service,
    create_app,
    error,
    is_row_id,
    json_object,
    monster_public,
    require_jwt,
    run_concurrently,
)
from encounter_flow import XP_LOSS, XP_WIN, resolve_and_save  # noqa: F401 - XP_* re-exported
from encounter_rules import REWARD_CHANCE
from flask import g, jsonify

app = create_app("encounters")
db = DB()

MAX_ATTEMPTS = 3
TEMPLATES_MAX_AGE = 30
REWARD_CONSTRAINT = "player_monsters_player_id_monster_id_key"


def _public(encounter: dict) -> dict:
    return {
        key: encounter[key]
        for key in ("id", "template_id", "team_id", "wild_monster_id", "outcome", "details")
    } | {"created_at": encounter.get("created_at")}


@app.get("/encounters")
@require_jwt
def available_encounters():
    catalogue = db.catalogue_by_id()  # O(1) per template instead of a scan of the catalogue
    return cacheable_json(
        [
            {
                "id": template["id"],
                "name": template["name"],
                "difficulty": template["difficulty"],
                "description": template["description"],
                "wild_monster": monster_public(wild)
                if (wild := catalogue.get(template["wild_monster_id"]))
                else None,
            }
            for template in db.select("encounter_templates", order_by="id", limit=50)
        ],
        TEMPLATES_MAX_AGE,
        public=False,  # only for the player's own browser: the list is behind a login
    )


@app.post("/encounters")
@require_jwt
def start_encounter():
    body = json_object()
    team_id, template_id = body.get("team_id"), body.get("template_id")
    if not (is_row_id(team_id) and is_row_id(template_id)):
        return error(400, "team_id and template_id must be positive integers")

    # The team (teams service) and the template (database) are independent: fetch both at once.
    token = bearer_token()
    team_response, template = run_concurrently(
        lambda: call_service("teams", "GET", f"/internal/teams/{team_id}", user_token=token),
        lambda: db.get("encounter_templates", template_id),
    )
    if team_response.status_code == 404:
        return error(404, "team not found")
    if team_response.status_code == 403:
        return error(403, "that team belongs to another player")
    team_response.raise_for_status()
    team = team_response.json()
    if team["player_id"] != str(g.player_id):
        return error(403, "that team belongs to another player")
    if template is None:
        return error(404, "encounter template not found")
    wild = db.catalogue_by_id().get(template["wild_monster_id"])
    if wild is None:
        return error(404, "wild monster not found")

    # Two concurrent wins can pick the same unowned reward; the unique constraint rejects the
    # loser, which re-reads the collection and picks again. Nothing of the failed attempt stays.
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result, failure = resolve_and_save(
                db, call_service, str(g.player_id), team, template, wild, REWARD_CHANCE, _public
            )
        except DbError as exc:
            if exc.conflicts_with(REWARD_CONSTRAINT) and attempt < MAX_ATTEMPTS:
                continue
            raise
        return failure if failure is not None else (jsonify(result), 201)
    raise AssertionError("unreachable")  # pragma: no cover


@app.get("/encounters/<id:encounter_id>")
@require_jwt
def encounter(encounter_id: int):
    row = db.get("encounters", encounter_id)
    if row is None or str(row["player_id"]) != str(g.player_id):
        return error(404, "encounter not found")
    return jsonify(_public(row))
