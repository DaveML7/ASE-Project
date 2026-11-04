"""Team composition rules (US-14, US-15, US-16). Pure functions, unit tested."""

TEAM_MAX_SIZE = 3
TEAM_MIN_SIZE = 1


def validate_team(member_ids: list[int], owned_player_monster_ids) -> list[str]:
    """Return the list of rule violations; empty means the team is valid."""
    owned = set(owned_player_monster_ids)
    problems = []
    if not TEAM_MIN_SIZE <= len(member_ids) <= TEAM_MAX_SIZE:
        problems.append(f"a team must have between {TEAM_MIN_SIZE} and {TEAM_MAX_SIZE} monsters")
    if len(set(member_ids)) != len(member_ids):
        problems.append("the same monster cannot be selected twice")
    missing = [str(member) for member in member_ids if member not in owned]
    if missing:
        problems.append(f"monsters not in your collection: {', '.join(missing)}")
    return problems
