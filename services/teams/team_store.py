"""Reading teams and asking the players service what a player owns (no HTTP routes here)."""

from ase_common import bearer_token, call_service
from team_rules import TEAM_MAX_SIZE, TEAMS_PER_PLAYER

NAME_MAX = 40


class TeamStore:
    def __init__(self, get_db):
        self._get_db = get_db  # resolved at call time (the module-level client can be swapped)

    @property
    def db(self):
        return self._get_db()

    def load(self, team_id: int) -> dict | None:
        return self.db.get("teams", team_id)

    def of_player(self, player_id: str) -> list[dict]:
        return self.db.select("teams", player_id=player_id, order_by="id", limit=TEAMS_PER_PLAYER)

    def count(self, player_id: str) -> int:
        return self.db.count("teams", player_id=player_id)

    def payloads(self, teams: list[dict]) -> list[dict]:
        """Payloads for several teams with ONE slot query (no N+1), in O(teams + slots)."""
        if not teams:
            return []
        slots = self.db.select(
            "team_slots", team_id__in=[team["id"] for team in teams], order_by="slot", limit=500
        )
        members: dict[int, list[int]] = {team["id"]: [] for team in teams}
        for slot in slots:
            members[slot["team_id"]].append(slot["player_monster_id"])
        return [
            {
                "id": team["id"],
                "name": team["name"],
                "member_ids": members[team["id"]],
                "max_size": TEAM_MAX_SIZE,
            }
            for team in teams
        ]

    def payload(self, team: dict) -> dict:
        return self.payloads([team])[0]


def owned_monster_ids(player_id: str) -> set[int]:
    """The player's own monster ids, asked to the players service with the player's token."""
    response = call_service(
        "players", "GET", f"/internal/players/{player_id}/monsters", user_token=bearer_token()
    )
    response.raise_for_status()
    return {entry["player_monster_id"] for entry in response.json()}


def member_ids_from(body: dict) -> list[int] | None:
    member_ids = body.get("member_ids")
    if not isinstance(member_ids, list) or not all(
        isinstance(member, int) and not isinstance(member, bool) for member in member_ids
    ):
        return None
    return member_ids


def name_from(body: dict, default: str | None) -> tuple[str | None, bool]:
    """Return (name, valid). A present name must be a non-blank string."""
    if body.get("name") is None:
        return default, True
    if not isinstance(body["name"], str) or not body["name"].strip():
        return None, False
    return body["name"].strip()[:NAME_MAX], True
