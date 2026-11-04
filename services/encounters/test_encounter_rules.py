from encounter_rules import element_advantage, pick_reward, resolve, team_power

WILD = {"name": "Flameox", "element": "fire", "hp": 78, "attack": 22, "defence": 11}


class FixedRandom:
    """Lets the tests pin the dice."""

    def __init__(self, roll=1.0):
        self.roll = roll

    def uniform(self, low, high):
        return self.roll

    def choices(self, population, weights=None, k=1):
        return [population[0]]


def member(attack=10, level=1, element="water"):
    return {"attack": attack, "level": level, "element": element}


def test_team_power_scales_attack_by_level():
    assert team_power([member(attack=10, level=3)]) == 30
    assert team_power([member(attack=10, level=2), member(attack=5, level=1)]) == 25


def test_element_advantage_counts_strong_members():
    assert element_advantage([member(element="water"), member(element="fire")], "fire") == 0.5
    assert element_advantage([], "fire") == 0.0


def test_strong_team_wins_and_weak_team_loses():
    strong = resolve([member(attack=60, level=3)] * 3, WILD, difficulty=1, rng=FixedRandom(0.85))
    weak = resolve([member(attack=2, level=1)], WILD, difficulty=1, rng=FixedRandom(1.15))
    assert strong["success"] is True
    assert weak["success"] is False


def test_higher_difficulty_raises_the_threshold():
    easy = resolve([member(attack=30)], WILD, difficulty=1, rng=FixedRandom())
    hard = resolve([member(attack=30)], WILD, difficulty=5, rng=FixedRandom())
    assert hard["threshold"] > easy["threshold"]


def test_pick_reward_returns_none_for_empty_catalogue():
    assert pick_reward([], rng=FixedRandom()) is None
    catalogue = [{"id": 1, "rarity": "common"}, {"id": 2, "rarity": "legendary"}]
    assert pick_reward(catalogue, rng=FixedRandom())["id"] == 1
