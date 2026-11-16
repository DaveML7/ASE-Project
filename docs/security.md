# Security design and evidence

Maps every security requirement of the course to what the project does and how it is
checked. Reuse it in the final report.

| Requirement | Implementation | Evidence |
| --- | --- | --- |
| Containers run in user mode | Services and `init` run as uid 10001, PostgreSQL as `postgres` (uid 70), the frontend as nginx uid 101. All app containers additionally have `cap_drop: ALL`, `no-new-privileges` and a read-only root filesystem. | `docker compose exec gateway id -u` → `10001`; `touch /x` fails; `CapEff: 0` |
| HTTPS/TLS everywhere, self-signed certificates | The `init` job creates a private CA and one ECDSA P-256 certificate per service. Every client verifies the CA (`CA_CERT`), nothing uses `verify=False` in the services. | `tests/unit/test_init_provisioning.py` (chain, SAN) |
| Deny-by-default gateway | The gateway forwards only routes in an explicit table (service + method + exact path regex); `..`, encoded dots, extra segments and unknown services give 404, wrong methods 405. The internal key is never sent by the gateway, and the gateway does not even have it. | `tests/unit/test_gateway_hardening.py`, `tests/integration/test_pentest_regressions.py` |
| Abuse limits | Sliding-window rate limits at the gateway: login per client and per client+username, registration per client, encounters per player (`RATE_LIMIT_*` env vars, `429` + `Retry-After`). | same tests |
| Inputs sanitised | Per-endpoint validation and length limits; `MAX_CONTENT_LENGTH` 64 KiB; SQL only through the db-manager allowlist + bound parameters; SVG text escaped. | Postman "invalid input" requests, login injection test |
| Sensitive data encrypted in the database | `credentials.password_hash` holds an scrypt hash that is **also** AES-256-GCM encrypted by the db-manager (`enc:v1:...`, column identity as associated data). The key exists only in the db-manager's directory. | `psql` on `credentials` shows `enc:v1:`; `services/db_manager/test_field_crypto.py` |
| OAuth2 + JWT, secure credentials | Resource-owner password flow shape: `POST /auth/login` → HS256 JWT (1 h), `Authorization: Bearer`, validated by the gateway and again by each service; ownership always taken from the token subject. Passwords: min. 8 chars, scrypt, constant-time compare, identical error for unknown user and wrong password. | Postman auth/acceptance collections |
| Static + dependency analysis, clean images | CI runs ruff, bandit and `pip-audit` (all dependencies, transitive ones pinned) and Trivy on every image (`--ignore-unfixed`, vulnerabilities + secrets, fails on HIGH/CRITICAL). | `.github/workflows/ci.yml` |

## Penetration test (own system, authorised)

Black-box probes against the running stack through the gateway. Findings and status:

| # | Finding | Severity | Status |
| --- | --- | --- | --- |
| 1 | `GET /api/v1/players/../internal/players/<victim>/monsters` returned any player's collection; `POST .../players/../internal/players` created arbitrary players; `/teams/../internal/teams/<id>` exposed any team. Cause: unvalidated path + the gateway attached the internal key to every forwarded request. | **Critical** | Fixed: route allowlist, internal key removed from the gateway, regression tests |
| 2 | Login took ~100 ms for an existing account and ~17 ms for an unknown one (user enumeration). | Medium | Fixed: a dummy scrypt hash is verified for unknown users |
| 3 | No throttling of login, registration or encounters (30 encounters took a new player to level 3 and 12 monsters in seconds). | Medium | Fixed: rate limits (see above) |
| 4 | `Server: gunicorn`, no HSTS/frame/CSP headers, TLS 1.2 accepted. | Low | Fixed: headers on every response, neutral `Server`, TLS 1.3 only, nginx CSP for the frontend |
| 5 | JWT had no issuer/audience check. | Low | Fixed: `iss`/`aud` claims are required and verified |
| 6 | Registration answers 409 for a taken username. | Info | Accepted: inherent to username-based sign-up; throttled |

Also checked, no issue found: SQL/identifier injection through the db-manager (allowlists +
bound parameters), `alg=none` and tampered/foreign-audience tokens, mass assignment on
`PATCH /players/me`, ownership of teams/encounters/history (IDOR), request size limits, CORS
(single configured origin), XSS (the frontend has no dynamic `innerHTML`).

, nothing in the images

There is **no** default password or key anywhere. On first start the `init` job
(`init/init.py`) generates, inside the `runtime` volume:

* `jwt_secret`, `internal_api_key`, `postgres_password` (random, 48 bytes URL-safe),
* `data_encryption_key` (32 random bytes, base64),
* a private CA and a TLS certificate/key per service.

**Least privilege:** the volume holds one directory per service and each container
mounts only its own (compose `volume.subpath`) read-only at `/run/app-config`:

| Service | Sees |
| --- | --- |
| gateway, auth, players, monsters, teams, encounters, history | its TLS key + `ca.crt`, `jwt_secret`, `internal_api_key` |
| db-manager | its TLS key + `ca.crt`, `internal_api_key`, `postgres_password`, `data_encryption_key` (**the only one**) |
| db | `postgres_password` only |
| nobody | `.master/` (CA private key and master copies) |

The code refuses to start with a missing, shorter than 32 characters or placeholder
(`change-me…`) secret (`common/ase_common/secrets.py`). Re-running `docker compose up`
keeps the keys; `docker compose down -v` creates fresh ones (and an empty database).

Trade-off: an RCE in a service exposes only that service's own secrets, but the
shared HS256 key means any service holding `jwt_secret` could mint tokens. An
asymmetric JWT (private key only in `auth`, public key elsewhere) would remove that;
it is listed in the report as future work.

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

* The gateway and the shared `jwt_secret` use HS256 (see trade-off above).
* Rate-limit counters live in the memory of the single gateway process (threaded, so
  they are exact); scaling the gateway to several processes or hosts would need a shared
  store such as Redis.
  There is no permanent account lockout by design (it would let an attacker lock victims out).
* No token revocation: a stolen token is valid until it expires (1 h).
* Sensitive-data encryption covers credentials; usernames, avatars and game data are
  not secret by design (the username is a public handle).

## Backup and recovery

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
