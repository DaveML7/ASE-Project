"""Resolving one encounter and persisting its consequences, without any HTTP concern.

Everything the encounter changes (the result, an optional reward, the experience) is written
in ONE database transaction; the rules themselves live in `encounter_rules`.
"""

from ase_common import bearer_token, error, monster_public
from encounter_rules import pick_reward, resolve

XP_WIN = 20
XP_LOSS = 5  # the level is derived from the XP by the database (db/init.sql)


def snapshot(members: list[dict], wild: dict, template: dict, outcome: dict, xp: int) -> dict:
    """What is stored with the encounter: enough to explain the result forever."""
    return {
        "template": template["name"],
        "difficulty": template["difficulty"],
        "wild_monster": monster_public(wild),
        "team": [
            {
                key: member[key]
                for key in (
                    "player_monster_id",
                    "monster_id",
                    "name",
                    "element",
                    "level",
                    "attack",
                    "defence",
                    "hp",
                )
            }
            for member in members
        ],
        "resolution": outcome,
        "xp_gained": xp,
    }


def operations(
    player_id: str,
    team_id: int,
    template: dict,
    wild: dict,
    won: bool,
    details: dict,
    reward: dict | None,
    xp: int,
) -> list[dict]:
    """The transaction: encounter row, optional reward, atomic XP increment (never read-modify-write)."""
    ops = [
        {
            "op": "insert",
            "table": "encounters",
            "values": {
                "player_id": player_id,
                "team_id": team_id,
                "template_id": template["id"],
                "wild_monster_id": wild["id"],
                "outcome": "win" if won else "loss",
                "details": details,
            },
        }
    ]
    if reward is not None:
        ops.append(
            {
                "op": "insert",
                "table": "player_monsters",
                "values": {"player_id": player_id, "monster_id": reward["id"], "source": "reward"},
            }
        )
    ops.append({"op": "increment", "table": "players", "id": player_id, "column": "xp", "by": xp})
    return ops


def resolve_and_save(
    db,
    call_service,
    player_id: str,
    team: dict,
    template: dict,
    wild: dict,
    reward_chance: float,
    public,
):
    """One attempt: read the collection, resolve, persist. Returns (body, None) or (None, failure)."""
    collection_response = call_service(
        "players", "GET", f"/internal/players/{player_id}/monsters", user_token=bearer_token()
    )
    if collection_response.status_code != 200:
        return None, error(502, "could not read your collection")
    collection = collection_response.json()
    by_player_monster = {entry["player_monster_id"]: entry for entry in collection}
    if not team["member_ids"] or any(m not in by_player_monster for m in team["member_ids"]):
        return None, error(422, "team has no usable monsters or contains monsters you do not own")
    members = [by_player_monster[member_id] for member_id in team["member_ids"]]

    outcome = resolve(members, wild, template["difficulty"])
    won = outcome["success"]
    xp = XP_WIN if won else XP_LOSS
    details = snapshot(members, wild, template, outcome, xp)

    reward = None
    if won:
        owned = {entry["monster_id"] for entry in collection}
        reward = pick_reward(
            [m for m in db.catalogue() if m["id"] not in owned], chance=reward_chance
        )
        if reward is not None:
            details["reward"] = monster_public(reward)

    results = db.batch(operations(player_id, team["id"], template, wild, won, details, reward, xp))
    encounter, player = results[0], results[-1]
    body = public(encounter)
    body["progress"] = {"xp_gained": xp, "xp": player["xp"], "level": player["level"]}
    return body, None
