# Audit report (black box, white box, fuzzing, concurrency)

Target: the full stack of branch `Alessio`, run with `docker compose` plus the test override.
Scripts: `tools/pentest/`. Scope: this project only.

## Method and results

| Technique | What was done | Result |
| --- | --- | --- |
| Black box | ~160 probes through the gateway: forged/tampered/alg-confusion JWTs, IDOR on teams/history/encounters, mass assignment, SQL/template injection strings, oversized and malformed bodies, CORS, TLS 1.1/1.2, header spoofing, method tampering, brute force and flooding | all handled (4xx/429), no stack traces |
| Direct access | every microservice port and the db-manager called without a service identity, with a forged service token and with a user token | refused (401/403/404) |
| Fuzzing | ~1100 mutated requests (types, huge ints, unicode, deep nesting, NUL, broken JSON, path and query tricks) on every writable endpoint | **0 server errors**, every error is `{"error","message"}` JSON, no internal detail leaked |
| Concurrency | 24 parallel encounters on one account; parallel team edits/deletes; 10 parallel registrations of one name | history rows == accepted encounters, XP == sum of results, exactly one account created |
| White box | bandit, pip-audit (both requirement files), search for dangerous calls (eval/exec/pickle/shell/verify=False), f-string SQL, secrets in the tree, compose and runtime hardening (`docker inspect`) | no finding |
| Isolation | per-service PostgreSQL roles tried against other services' tables, DDL and DROP | `permission denied` (`tests/integration/test_db_isolation_live.py`) |
| Load / memory | Locust 40 users, 45 s: 1031 requests, 0 failures; container memory flat | ok |
| Regression | 307 pytest, 13 Newman runs (0 failed assertions) | ok |

## Findings fixed during the audit

| # | Finding | Impact | Fix |
| --- | --- | --- | --- |
| 1 | A NUL character in a login name reached PostgreSQL and became a 500 in db-manager / **502** at the gateway | error-based probing, noisy failures | every service rejects NUL (raw, `\u0000`, `%00`) with 400 (`reject_nul_bytes`); login checks the username shape before any query |
| 2 | A 5000-character login name made the db-manager request line too large: **502** | same | username shape check (cheap, still one hash check, same 401 answer) |
| 3 | Broken JSON was silently read as an empty body (login answered 401, others "missing field") | confusing contract | `json_object` answers **400** "not valid JSON" |
| 4 | Ids `0`, negative or above 2^63-1 (`/teams/0`, `/encounters/0`, `team_id` in the body) reached the database and returned **502** | a client error looked like an outage | `<id:...>` URL converter (404) and `is_row_id` validation (400) |
| 5 | Team names longer than 40 characters were cut in silence | surprising contract | now a 400 |
| 6 | The database container ran with a writable root filesystem and all capabilities | weaker container hardening | `read_only: true`, `cap_drop: [ALL]`, tmpfs for the socket directory |
| 7 | CI failed on the image scan: libxml2 CVE-2026-86144 (HIGH) in the db image | requirement "no HIGH/CRITICAL" | `apk upgrade` in `db/Dockerfile` |

## Behaviour that is intentional

* `/health` of the services answers only to the container itself (404 from the host).
* A team created without a name is called "Team".
* Rate limiting answers 429 with `Retry-After`; the limits are per account and per address.

## Residual risk

* Rate limiting state lives in the single gateway process (documented in `docs/security.md`).
* The scans cover the code and the images at the time of the run; new CVEs appear daily, so keep the CI scan.
