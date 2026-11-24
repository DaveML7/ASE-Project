# Security design and evidence

Maps every security requirement of the course to what the project does and how it is
checked. Reuse it in the final report.

| Requirement | Implementation | Evidence |
| --- | --- | --- |
| Containers run in user mode | Services and `init` run as uid 10001, PostgreSQL as `postgres` (uid 70), the frontend (nginx from Alpine) as uid 101. All app containers additionally have `cap_drop: ALL`, `no-new-privileges` and a read-only root filesystem. | `docker compose exec gateway id -u` → `10001`; `touch /x` fails; `CapEff: 0` |
| HTTPS/TLS everywhere, self-signed certificates | The `init` job creates a private CA and one ECDSA P-256 certificate per service, including PostgreSQL. HTTP clients verify the CA (`CA_CERT`); the PostgreSQL connection requires TLS 1.3 and libpq `verify-full`. | `tests/unit/test_init_provisioning.py` (chain, SAN) |
| Deny-by-default gateway | The gateway forwards only routes in an explicit table (service + method + exact path regex); `..`, encoded dots, extra segments and unknown services give 404, wrong methods 405. The gateway holds no secret and adds no credential to forwarded requests. | `tests/unit/test_gateway_hardening.py`, `tests/integration/test_pentest_regressions.py` |
| Abuse limits | Sliding-window rate limits at the gateway: login per client and per client+username, registration per client, encounters per player (`RATE_LIMIT_*` env vars, `429` + `Retry-After`). | same tests |
| Inputs sanitised | Per-endpoint validation and length limits; `MAX_CONTENT_LENGTH` 64 KiB; SQL only through the db-manager allowlist + bound parameters; SVG text escaped. | Postman "invalid input" requests, login injection test |
| Sensitive data encrypted in the database | `credentials.password_hash` holds an scrypt hash that is **also** AES-256-GCM encrypted by the db-manager (`enc:v1:...`, column identity as associated data). The key exists only in the db-manager's directory. | `psql` on `credentials` shows `enc:v1:`; `services/db_manager/test_field_crypto.py` |
| OAuth2 + JWT, secure credentials | Resource-owner password flow shape: `POST /auth/login` → EdDSA JWT (30 min, `iss`/`aud`/`jti`), `Authorization: Bearer`. Only the auth service holds the signing key; everyone else verifies with the public key. `POST /auth/logout` revokes the token (blocklist enforced at the gateway). Passwords: min. 8 chars, scrypt, constant-time compare, identical error and time for unknown user and wrong password. | Postman auth/acceptance collections, `test_hardening_helpers`, `test_gateway_hardening` |
| Service-to-service identity and least privilege | Ed25519 service tokens (60 s) verified with the caller's public key, distributed only to allowed callers; the db-manager applies a per-caller table/operation ACL; internal endpoints also need the owner's user token. No shared secret exists. | `test_service_identity`, `test_db_access_control`, `test_service_identity_live` |
| Static + dependency analysis, clean images | CI runs ruff, bandit and `pip-audit` (all dependencies, transitive ones pinned) and Trivy on every image (`--ignore-unfixed`, vulnerabilities + secrets, fails on HIGH/CRITICAL). | `.github/workflows/ci.yml` |

## Penetration test (own system, authorised)

Black-box probes against the running stack through the gateway. Findings and status:

| # | Finding | Severity | Status |
| --- | --- | --- | --- |
| 1 | `GET /api/v1/players/../internal/players/<victim>/monsters` returned any player's collection; `POST .../players/../internal/players` created arbitrary players; `/teams/../internal/teams/<id>` exposed any team. Cause: unvalidated path + the gateway attached the internal key to every forwarded request. | **Critical** | Fixed: route allowlist; the shared internal key was later removed altogether (asymmetric service identity), regression tests |
| 2 | Login took ~100 ms for an existing account and ~17 ms for an unknown one (user enumeration). | Medium | Fixed: a dummy scrypt hash is verified for unknown users |
| 3 | No throttling of login, registration or encounters (30 encounters took a new player to level 3 and 12 monsters in seconds). | Medium | Fixed: rate limits (see above) |
| 4 | `Server: gunicorn`, no HSTS/frame/CSP headers, TLS 1.2 accepted. | Low | Fixed: headers on every response, neutral `Server`, TLS 1.3 only, nginx CSP for the frontend |
| 5 | JWT had no issuer/audience check. | Low | Fixed: `iss`/`aud` claims are required and verified |
| 6 | Registration answers 409 for a taken username. | Info | Accepted: inherent to username-based sign-up; throttled |

Also checked, no issue found: SQL/identifier injection through the db-manager (allowlists +
bound parameters), `alg=none` and tampered/foreign-audience tokens, mass assignment on
`PATCH /players/me`, ownership of teams/encounters/history (IDOR), request size limits, CORS
(single configured origin), XSS (the frontend has no dynamic `innerHTML`).

## Secrets and keys: nothing in the repository, nothing in the images

There is **no** default password or key anywhere, and **no shared secret at all**. On first
start the `init` job (`init/init.py`) generates, inside the `runtime` volume:

* separate random `postgres_password` (database bootstrap) and `db_app_password` (restricted login), and `data_encryption_key` (AES-256, 32 random bytes),
* an **Ed25519 key pair per calling service** (service identity) and one for user tokens,
* a private CA and a TLS certificate/key per service.

**Least privilege:** the volume holds one directory per service and each container mounts
only its own (compose `volume.subpath`) read-only at `/run/app-config`:

| Service | Sees |
| --- | --- |
| gateway | TLS, its service key, `jwt_public.pem` (verify only) |
| auth | TLS, its service key, `jwt_private.pem` (**only holder**), `jwt_public.pem`, `gateway.pub` |
| players | TLS, service key, `jwt_public.pem`, `auth.pub` `teams.pub` `encounters.pub` |
| teams | TLS, service key, `jwt_public.pem`, `encounters.pub` |
| monsters, history, encounters | TLS, service key (+ `jwt_public.pem` except monsters) |
| db-manager | TLS, `db_app_password`, `data_encryption_key` (**only application holder**), public keys of the 6 domain services |
| db | TLS, `postgres_password` (bootstrap), `db_app_password` (application role) |
| nobody | `.master/` (CA key and master copies of all private keys) |

A service can therefore **verify** but never **forge**: it has only the public keys of its
permitted callers and the user-token public key. A compromised service can:

* sign as itself only (it cannot impersonate another service or mint user tokens);
* call only the endpoints whose owner granted its public key;
* touch only the db tables and operations of its role (e.g. `monsters` reads the
  catalogue; only `auth` reaches credentials; only `encounters` adds XP; history cannot be erased);
* reach a player's data on an internal endpoint only while holding that player's own token.

`/health` of every service answers only to the container's own loopback healthcheck (404
for other containers or hosts); the gateway's is public and returns just `{"status":"ok"}`.
The db-manager additionally has a loopback-only `/ready` probe that verifies it can query PostgreSQL.

Re-running `docker compose up` keeps all keys; `docker compose down -v` creates fresh
ones (and an empty database).

## Database isolation

Each microservice has its own PostgreSQL schema and its own NOLOGIN role; the db-manager switches
to the caller's role for every request (`SET LOCAL ROLE`), so cross-service reads and DDL are
refused by PostgreSQL itself, not only by application code. See `docs/architecture.md`.

## Atomic progress (US-25)

XP changes use `UPDATE … SET xp = xp + n` inside the same transaction that stores the
encounter and the reward; the level is a generated column. A unique constraint on
`(player_id, monster_id)` makes a duplicate reward impossible and the loser of a race
retries. Proven by `tests/integration/test_progress_integrity.py` (12 simultaneous
encounters) and by the unit tests of the encounters service.

## Image scanning notes

* Python images are scanned with `--ignore-unfixed`: Debian ships a number of
  util-linux/`libacl` HIGH findings with *no* fixed package yet; those cannot be removed
  by the project and disappear when Debian publishes fixes (the base image is rebuilt on
  every CI run). Fixable findings (e.g. `zlib1g`) are removed by the `apt-get
  --only-upgrade` step in the Dockerfile.
* The PostgreSQL image is pinned to a patched release and the unused privilege-dropping
  helper `gosu` (a Go binary with known stdlib CVEs) is deleted because the container
  always runs as `postgres`.

## Known limitations (documented, not hidden)

* Revocation is enforced at the gateway, the only public entry point. Services behind it
  trust the signature and the 30-minute expiry; a token stolen from inside the network
  could outlive a logout by at most that time. The blocklist is refreshed every 5 s; if the
  auth service is down the gateway keeps the last successful list. After startup, protected
  requests return `503` until the gateway has loaded its first list.
* A compromised service can still do what its own role allows (for example `encounters`
  can add XP). Rights are minimal but not zero; mTLS between services and network
  policies in a service mesh would be the next hardening step.
* Service tokens are valid for 60 s and carry a `jti`, but replay within that window is
  not tracked.
* Rate-limit counters live in the memory of the single gateway process (threaded, so
  they are exact); several processes or hosts would need a shared store such as Redis.
* No permanent account lockout by design (it would let an attacker lock victims out).
* Sensitive-data encryption covers credentials; usernames, avatars and game data are not
  secret by design (the username is a public handle).

## Backup and recovery

For an existing volume that predates the restricted PostgreSQL login, follow the
[non-destructive upgrade](db-manager-audit.md#upgrade-an-existing-compose-deployment).

The database and the keys that decrypt it are separate volumes and **must be backed up
together**. Losing `runtime` while keeping `db_data` makes the stored credentials
undecryptable (logins answer 500) and the database password unknown.

```bash
# backup (stack stopped)
docker run --rm -v monster-tamers_runtime:/r -v monster-tamers_db_data:/d -v "$PWD":/b alpine \
  tar czf /b/monster-tamers-backup.tgz -C / r d
```

Restore the archive into freshly created volumes with the reverse `tar xzf`. Never run
`docker volume rm` on only one of the two.
