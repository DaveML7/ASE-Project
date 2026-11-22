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
  which is exactly what a db-manager creates. One db-manager means one shared
  schema and one application service that issues SQL.
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

`credentials` has no FK to `players`: auth stores a pending credential before asking
players to create the profile. A retry with the same credentials reuses the player ID;
profile creation acknowledges a matching existing profile and does not duplicate starters.
Login is enabled only after auth marks registration complete.

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
  the catalogue, only `auth` touches credentials, and nobody can erase history.
  Profile updates only write avatar/faction; `encounters` alone can increment XP or write
  results. PostgreSQL requires verified TLS and an application login that does not own the schema.
* Internal endpoints (`/internal/*`) need the service token AND the end user's own JWT,
  whose subject must own the resource: a service cannot read data of a player whose token
  it does not hold.
* Logout stores the token id in `revoked_tokens`; the gateway refreshes that list every
  5 seconds (and updates it instantly on logout) and refuses revoked tokens. Auth reads
  all pages of active revocations; the gateway keeps locally acknowledged logouts until
  their token expires, independently of refreshes.
* Ownership is enforced server-side from the token subject: teams, encounters and
  history are filtered by `player_id`, never by a client-supplied id.
* Secrets and TLS keys are generated at first start and delivered per service
  (`init` job, volume subpaths); see [security.md](security.md).

## Atomic progress and concurrency

Team edits lock the team row and replace the name and slots in one transaction.
The persistence service also checks that all selected monsters belong to the team owner.

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

## Resilience, performance and robustness

Everything below lives once in `common/ase_common` and is used by every service.

| Concern | Mechanism | Where |
| --- | --- | --- |
| A dependency is slow or down | **Circuit breaker** per target (5 failures, then 503 + `Retry-After`, one trial call after 10 s), separate connect (2 s) and read (10 s) timeouts, bounded **retry** only for safe calls (connection errors, and GET on 502/503/504) | `clients.py` (`guarded_request`) |
| Timeouts and overload are told apart | timeout is 504, open circuit 503, other upstream failures 502; the db-manager maps pool exhaustion to 503 | `app.py` error handlers, `db_manager` |
| Reads of rarely changing data | **Cache-aside** with TTL, refresh under a lock (no stampede) and **stale-if-error**: the catalogue is still served while the database layer is down | `DB.catalogue()` |
| Repeated work | service tokens are signed once per 40 s per audience; independent lookups (team + template) run concurrently on a bounded pool | `auth.py`, `concurrency.py` |
| Memory growth | rate-limit table swept every window and hard-capped; caches hold a constant number of entries; gunicorn recycles workers (`max_requests` + jitter; off for the gateway, which keeps limiter state); containers have `mem_limit`, `pids_limit` and rotated logs | `ratelimit.py`, `gunicorn.conf.py`, compose |
| Bad input | one `json_object()` (non-object body is a 400, never a 500), one `parse_limit()`, explicit type checks instead of `str()` coercion | `http.py` |
| Following one request | `X-Request-ID` is created at the edge, forwarded on every internal call, echoed in the response and printed in the access log of each service | `app.py`, `clients.py` |

Patterns in use: **application factory** (`create_app`), **decorator** (`require_jwt`,
`require_service`), **circuit breaker**, **retry with backoff**, **cache-aside with
stale-if-error**, **bulkhead** (per-service key material and per-caller database ACL),
**adapter/gateway** (`DB` client in front of the db-manager), **strategy** (injectable
random generator in the game rules), **pure domain functions** (team and encounter rules
are free of I/O and unit tested) and a single **route table** in the gateway.

Proof: `tests/unit/test_resilience.py`, `tests/unit/test_input_hardening.py` and the live
`tests/integration/test_resilience_live.py`, which stops the db-manager and checks that
failures are fast and the system recovers on its own.

## Placeholder / deliberate shortcuts

* Revocation is enforced at the gateway (the only public entry point); services behind it
  trust the signature and the 30-minute expiry (see security.md).
* Monster artwork is an SVG rendered on demand instead of stored assets, so the
  repository stays asset-free.
* Encounter outcomes use `random` for the roll. Determinism per encounter would be
  needed for reproducibility; not required by the specification.
* Teams are limited to 3 monsters; raising the limit only changes
  `services/teams/team_rules.py` and the `team_slots.slot` constraint.
