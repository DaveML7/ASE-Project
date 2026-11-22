"""Encounter resolution rules (US-18, US-19).

Documented in docs/game-rules.md. Pure functions so they can be unit tested.
"""

import random

ELEMENT_STRONG_AGAINST = {
    "fire": "nature",
    "nature": "water",
    "water": "fire",
    "air": "earth",
    "earth": "air",
}
ELEMENT_BONUS = 0.15
ROLL_RANGE = (0.85, 1.15)
RARITY_WEIGHTS = {"common": 6.0, "rare": 3.0, "epic": 1.5, "legendary": 0.5}
REWARD_CHANCE = 0.5  # share of wins that also award a monster (see docs/game-rules.md)

# Outcomes and rewards must not be predictable from earlier results: the default
# generator draws from the operating system, not from a seedable Mersenne Twister.
_RNG = random.SystemRandom()


def team_power(members: list[dict]) -> float:
    """Attack scaled by level; every member counts."""
    return sum(member["attack"] * max(member["level"], 1) for member in members)


def element_advantage(members: list[dict], wild_element: str) -> float:
    """Share of the team that is strong against the wild element (0.0 .. 1.0)."""
    if not members:
        return 0.0
    strong = sum(
        1 for member in members if ELEMENT_STRONG_AGAINST.get(member["element"]) == wild_element
    )
    return strong / len(members)


def resolve(members: list[dict], wild: dict, difficulty: int, rng=None) -> dict:
    """Compare team power against a difficulty-scaled threshold.

    A random roll in ROLL_RANGE keeps both outcomes possible for close matchups.
    """
    rng = rng or _RNG
    power = team_power(members)
    threshold = (wild["defence"] * 3 + wild["hp"]) * (1 + (difficulty - 1) * 0.15)
    advantage = element_advantage(members, wild["element"])
    score = power * (1 + ELEMENT_BONUS * advantage) * rng.uniform(*ROLL_RANGE)
    return {
        "success": score >= threshold,
        "score": round(score, 2),
        "threshold": round(threshold, 2),
        "team_power": round(power, 2),
        "element_advantage": round(advantage, 2),
    }


def pick_reward(catalogue: list[dict], rng=None, chance: float = 1.0) -> dict | None:
    """Rarity-weighted reward, granted with probability `chance`.

    Returns None when the catalogue is empty or the roll misses.
    """
    rng = rng or _RNG
    if not catalogue or (chance < 1.0 and rng.random() >= chance):
        return None
    weights = [RARITY_WEIGHTS.get(monster["rarity"], 1.0) for monster in catalogue]
    return rng.choices(catalogue, weights=weights, k=1)[0]
