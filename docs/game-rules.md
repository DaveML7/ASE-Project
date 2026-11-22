# Game rules

The rules below are the ones the scaffold implements. Change them here first, then
in the matching pure functions, then in the unit tests.

## Collection

* A new player receives 3 starter monsters (lowest catalogue ids, `source='starter'`).
* Winning an encounter awards one monster the player does not own yet with probability
  50% (`REWARD_CHANCE`), picked with rarity weights: common 6, rare 3, epic 1.5, legendary 0.5 (`pick_reward`).
* If the player owns every catalogue monster, a win awards nothing (no duplicates).

## Monster characteristics

Every monster has `element`, `hp`, `attack`, `defence` and `rarity`, and an image at
`/monsters/{id}/image`. Element is one of fire, water, nature, air, earth.

## Teams

* Between 1 and 3 monsters (`TEAM_MAX_SIZE`), all from the player's collection.
* At most 20 teams per player (`TEAMS_PER_PLAYER`); a 21st is refused with 409 until one is
  deleted. A team name, when given, must be a non-blank string (max. 40 characters).
* The same monster cannot appear twice in a team.
* Validation is a pure function (`services/teams/team_rules.py`) reused by create,
  update and `POST /teams/{id}/validate`.

## Encounters

1. The player picks an encounter template and one of their teams.
2. The service fetches the team (teams service) and the collection (players service);
   it never trusts client-side monster data.
3. Resolution:
   * `team_power = sum(attack * level)` over the team members.
   * `threshold = (wild.defence * 3 + wild.hp) * (1 + (difficulty - 1) * 0.15)`.
   * Element advantage: the share of team members whose element beats the wild
     element (fire beats nature, nature beats water, water beats fire; air and earth beat each other) gives
     `+15%` score per full share.
   * `score = team_power * (1 + 0.15 * advantage) * uniform(0.85, 1.15)`.
   * Win if `score >= threshold`.
   The random numbers (roll and reward) come from the operating system (`SystemRandom`),
   so earlier results cannot be used to predict later ones. A team containing a monster
   the player no longer owns is refused (422) instead of fighting with fewer members.
4. Outcomes: `win` (+20 XP, possible reward) or `loss` (+5 XP, no reward).
5. Level is `1 + xp / 100` (integer division), computed by the database as a generated
   column: no service can write it, so level and XP cannot disagree.
6. Encounter row, reward and XP increase are written in one transaction, and the XP
   is changed with an atomic `xp = xp + n` (never read-modify-write), so concurrent
   encounters cannot overwrite each other. If two simultaneous wins pick the same
   reward, the unique constraint rejects the second and the service retries (up to 3
   attempts, re-reading the collection) before answering 502. Both
   outcomes are always possible: a weak team can only win on a lucky roll, a very
   strong team can only lose on an unlucky one, and mid-strength teams vary.

## Lifecycle and contingencies

* An encounter is created and resolved in the same request; there is no dangling
  "open" encounter state to clean up (documented simplification, US-21 stays optional).
* If the teams or players service is unreachable, the encounter request fails with
  502 and nothing is persisted (no partial reward, no XP change).
* Deleting a team does not touch history: the team snapshot lives in the result.
