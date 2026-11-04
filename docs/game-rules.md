# Game rules

The rules below are the ones the scaffold implements. Change them here first, then
in the matching pure functions, then in the unit tests.

## Collection

* A new player receives 3 starter monsters (lowest catalogue ids, `source='starter'`).
* Winning an encounter may award one monster the player does not own yet, picked
  with rarity weights: common 6, rare 3, epic 1.5, legendary 0.5 (`pick_reward`).
* If the player owns every catalogue monster, a win awards nothing (no duplicates).

## Monster characteristics

Every monster has `element`, `hp`, `attack`, `defence` and `rarity`, and an image at
`/monsters/{id}/image`. Element is one of fire, water, nature, air, earth.

## Teams

* Between 1 and 3 monsters (`TEAM_MAX_SIZE`), all from the player's collection.
* The same monster cannot appear twice in a team.
* Validation is a pure function (`services/teams/team_rules.py`) reused by create,
  update and `POST /teams/{id}/validate`.

## Encounters

1. The player picks an encounter template and one of their teams.
2. The service fetches the team (teams service) and the collection (players service);
   it never trusts client-side monster data.
3. Resolution:
   * `team_power = sum(attack * level)` over the team members.
   * `threshold = (wild.defence * 5 + wild.hp * 2) * (1 + (difficulty - 1) * 0.15)`.
   * Element advantage: the share of team members whose element beats the wild
     element (fire > nature > water > fire, air > earth > earth > air) gives
     `+15%` score per full share.
   * `score = team_power * (1 + 0.15 * advantage) * uniform(0.85, 1.15)`.
   * Win if `score >= threshold`.
4. Outcomes: `win` (+20 XP, possible reward) or `loss` (+5 XP, no reward).
5. Level is `1 + xp // 100`.
6. Encounter row, reward and XP/level update are written in one transaction. Both
   outcomes are always possible: a weak team can only win on a lucky roll, a very
   strong team can only lose on an unlucky one, and mid-strength teams vary.

## Lifecycle and contingencies

* An encounter is created and resolved in the same request; there is no dangling
  "open" encounter state to clean up (documented simplification, US-21 stays optional).
* If the teams or players service is unreachable, the encounter request fails with
  502 and nothing is persisted (no partial reward, no XP change).
* Deleting a team does not touch history: the team snapshot lives in the result.
