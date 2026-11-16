"""Single definition of how a monster is exposed by every service."""

IMAGE_PATH = "/monsters/{id}/image"


def monster_public(monster: dict) -> dict:
    """Catalogue monster as seen by clients (and stored in encounter snapshots)."""
    return {
        "id": monster["id"],
        "name": monster["name"],
        "element": monster["element"],
        "hp": monster["hp"],
        "attack": monster["attack"],
        "defence": monster["defence"],
        "rarity": monster["rarity"],
        "description": monster["description"],
        "image_url": IMAGE_PATH.format(id=monster["id"]),
    }


def collection_entry(owned: dict, monster: dict) -> dict:
    """A monster owned by a player: catalogue data plus the player's own fields."""
    entry = monster_public(monster)
    entry.pop("id")
    return {
        "player_monster_id": owned["id"],
        "monster_id": monster["id"],
        **entry,
        "nickname": owned["nickname"],
        "level": owned["level"],
    }
