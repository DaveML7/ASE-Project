# Architecture and design decisions

Short rationale list to reuse in the final report. Keep it in sync when the design changes.

## Service boundaries

| Service | Owns | Backlog |
| --- | --- | --- |
| auth | credentials, tokens | US-01, US-02, US-05 |
| players | profile, level/XP, collection | US-03, US-04, US-06, US-09, US-12 (write side) |
| monsters | catalogue, artwork | US-10, US-11 |
| teams | teams and team rules | US-14, US-15, US-16 |
| encounters | encounters, resolution, rewards | US-17..US-20 |
| history | read model of past encounters | US-23, US-24 |
| gateway | routing, token validation, CORS | US-28 |

History reads the `encounters` table written by the encounters service; it never
writes. This keeps "who may change a result" unambiguous (no client-supplied
outcomes) while allowing a separate read service.

## Persistence: one Postgres plus a db-manager

* The course material allows the "Endpoint-based interaction" architectural smell,
  which is exactly what a db-manager creates. One db-manager means one schema,
  one migration file and one place where SQL exists.
* `db-manager` only accepts allowlisted tables/columns and parameterised values;
  callers cannot inject identifiers or statements.
* `POST /db/batch` runs several operations in one transaction. This is what makes
  "encounter resolved + reward granted + XP updated" atomic (US-19, US-12, US-25).
* Trade-off: the database is a single point of failure and services sharing tables
  reduce independence. Acceptable at this scale and documented as a deliberate choice.

## Database image and volume

`db/Dockerfile` copies `db/init.sql` into the image instead of bind-mounting it:
a bind mount fails when the repository lives in a home directory the `postgres`
user cannot traverse. The service also sets `user: postgres` so the database
container is unprivileged; a fresh named volume inherits the image's directory
ownership, so first-run `initdb` works as uid 70. The image is pinned to a patched
PostgreSQL release and the unused `gosu` helper is removed.

`players.level` is a generated column (`1 + xp / 100`): level and XP cannot drift apart
and no service can write the level.

## No cross-service foreign keys except inside one owner

`credentials` has no FK to `players`: the auth service writes credentials before it
asks the players service to create the profile. Compensating delete rolls the
credential back if the profile cannot be created.

`encounters.team_id` has no FK either: a team can be edited or deleted later while
history must survive. The resolved team is snapshotted in `encounters.details`.

## Authentication and authorisation

* Credentials (username/password) are exchanged for an EdDSA-signed JWT (`POST /auth/login`),
  following the OAuth2 password-grant shape used in the Access Security lab.
* The gateway validates the token before forwarding, and each service validates it
  again in depth. Tokens are short-lived (`JWT_TTL_SECONDS`, default 1h).
* Service-to-service identity is asymmetric (Ed25519): each service signs a 60-second
  token with its own private key and the callee verifies it with the caller's public key,
  which the init job only hands to allowed callers. There is no shared secret.
* The db-manager maps the proven caller to a table/operation ACL: `monsters` can only read
  the catalogue, only `auth` touches credentials, only `encounters` can add XP or write
  results, nobody can erase history.
* Internal endpoints (`/internal/*`) need the service token AND the end user's own JWT,
  whose subject must own the resource: a service cannot read data of a player whose token
  it does not hold.
* Logout stores the token id in `revoked_tokens`; the gateway refreshes that list every
  5 seconds (and updates it instantly on logout) and refuses revoked tokens.
* Ownership is enforced server-side from the token subject: teams, encounters and
  history are filtered by `player_id`, never by a client-supplied id.
* Secrets and TLS keys are generated at first start and delivered per service
  (`init` job, volume subpaths); see [security.md](security.md).

## Atomic progress and concurrency

`encounters` resolves an encounter and persists the result, the optional reward and the
XP increase in **one** `db-manager` batch transaction. XP uses an atomic increment op
(`xp = xp + n`), never read-modify-write. Two simultaneous wins may pick the same
unowned reward: the unique constraint rejects the second, which re-reads the
collection and retries (max 3 attempts). `tests/integration/test_progress_integrity.py`
fires 12 parallel encounters and checks XP, history and rewards.

## Testing strategy

* **Unit** (`pytest`): game rules, field encryption, secret loading, the provisioning
  job and the encounters handler with in-memory fakes.
* **Component** (Newman, `docs/postman/<service>.postman_collection.json`): each
  microservice is called directly on its own port (test override publishes them on
  loopback); one valid and one invalid request per endpoint.
* **Integration**: the *same* collections, base URL switched to the gateway, plus the
  end-to-end `acceptance` collection mapped to user-story ids.
* **Performance** (Locust): gateway only, core loop (`tests/locust`).
* The collections are generated from `tests/api/build_collections.py`; CI fails if the
  committed JSON drifts from the generator.

## Placeholder / deliberate shortcuts

* Revocation is enforced at the gateway (the only public entry point); services behind it
  trust the signature and the 30-minute expiry (see security.md).
* Monster artwork is an SVG rendered on demand instead of stored assets, so the
  repository stays asset-free.
* Encounter outcomes use `random` for the roll. Determinism per encounter would be
  needed for reproducibility; not required by the specification.
* Teams are limited to 3 monsters; raising the limit only changes
  `services/teams/team_rules.py` and the `team_slots.slot` constraint.
