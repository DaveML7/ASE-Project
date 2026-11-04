# Project backlog (to be completed by the group)

Copy the mandatory rows of the "Project backlog" sheet, then fill in *Acceptance
criteria* (Given / When / Then, observable behaviour) and *Dependencies* (story ids
or "none"). The table below is the scaffold's starting point — verify each row
against the code before submitting.

| ID | Title | Acceptance criteria | Dependencies | Status in code |
| --- | --- | --- | --- | --- |
| US-01 | Create an account | Given an unused username, When I register with a valid password, Then an account and a starter collection exist and a token is returned. Given a taken username, Then 409. | none | done |
| US-02 | Log in | Given valid credentials, When I log in, Then I receive a JWT. Given a wrong password, Then 401. | US-01 | done |
| US-03 | Resume saved progress | Given I return after logging in, Then my level, XP and collection are unchanged. | US-01, US-06 | done |
| US-05 | Protect account data | Given no or an invalid token, Then every player endpoint answers 401; given another player's team id, Then 404/403. | US-01, US-02 | partial (see docs/architecture.md) |
| US-06 | Initial collection | Given a new account, Then 3 monsters are owned. | US-01 | done |
| US-09 | View collection | Given I own monsters, When I read `/players/me/monsters`, Then each has id, element, stats and level. | US-06 | done |
| US-10 | Browse catalogue | Given the seeded catalogue, Then it lists all monsters without a token. | none | done |
| US-11 | View monster details | Given a monster id, Then details and an image URL are returned; unknown ids give 404. | US-10 | done |
| US-12 | Receive an earned monster | Given a won encounter that awards a monster, Then it appears in my collection and survives a restart. | US-06, US-19 | done |
| US-14 | Create a team | Given owned monsters, When I create a team, Then it is stored with its members. | US-09 | done |
| US-15 | Modify a team | Given an existing team, When I replace its members, Then only the new members remain. | US-14 | done |
| US-16 | Validate a team | Given a team with a monster I do not own or more than 3 monsters, Then it is rejected with violations. | US-14 | done |
| US-17 | Browse encounters | Given a logged-in player, Then templates with wild monster and difficulty are listed. | US-02 | done |
| US-18 | Start an encounter | Given a valid team and template, Then an encounter is created and resolved server-side. | US-14, US-17 | done |
| US-19 | Resolve an encounter | Given a valid team, Then the result follows docs/game-rules.md and both win and loss are possible. | US-18 | done |
| US-20 | View result details | Given an encounter id I own, Then the outcome, team and resolution values are returned. | US-18 | done |
| US-23 | View history | Given past encounters, Then they are listed newest first for the caller only. | US-18 | done |
| US-24 | View past encounter details | Given a past encounter I own, Then the full snapshot is returned; another player's gives 404. | US-23 | done |
| US-25 | Protect saved progress | Given two concurrent encounter requests, Then XP/rewards stay consistent (single transaction). | US-12, US-19 | partial |
| US-28 | Simple interface | Given the backend running, Then the frontend performs register, login, team and encounter flows through the gateway only. | US-02 | done |

Optional stories (US-07, US-08, US-13, US-21, US-22, US-26, US-27) are not
implemented; their rows can be added if the group picks them up.
