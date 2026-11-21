# db-manager audit and fixes

Scope: the persistence service, PostgreSQL deployment and schema, and the callers that depend on persistence for logout, signup, teams, and progress. The review assumed a single Compose deployment with concurrent players. Keep `db-manager`: it is the SQL boundary and already keeps encounter results, rewards, and XP in one transaction. The SQL paths reviewed allowlist identifiers and bind values; the audit found no SQL injection in those paths.

## Findings and changes

| # | Finding | Change and verification |
| --- | --- | --- |
| 1 | The gateway could forget logged-out tokens: auth returned only the first 500 active revocations, and refresh replaced the entire cache. | [`auth`](../services/auth/app.py) reads all active pages by ID. The [`gateway`](../services/gateway/app.py) replaces its remote cache only after a successful response, serializes refreshes, and keeps locally acknowledged logouts separately until expiration. Before the first successful refresh it does not accept protected requests. Unit tests cover a 501st result, a refresh preserving a local logout, and auth downtime. The live suite also restarts the gateway and verifies the token remains rejected. |
| 2 | Database downtime looked like a constraint conflict. Logout could acknowledge a revocation that was never stored; signup and encounters handled unrelated conflicts as expected ones. | [`db-manager`](../services/db_manager/app.py) distinguishes invalid data (`400`), constraints (`409` with a constraint name), unavailable or transient failures (`503`), and unexpected database errors (`500`). It no longer logs raw PostgreSQL error details. Auth tolerates only a duplicate revocation, and the encounter service retries only the duplicate reward constraint. Unit tests cover outage, unrelated constraints, and reward retry. |
| 3 | The players service could overwrite XP, ID, or username through profile updates. | PATCH and batch player updates allow only `avatar` and `faction`. Player inserts use the database XP default; the only application operation allowed to increment it is the encounters role. A live test checks both write routes and confirms XP remains unchanged. PostgreSQL also denies XP on insert for the application role. |
| 4 | PostgreSQL did not require verified TLS, and `db-manager` connected as the bootstrap superuser. | [`init`](../init/init.py) provisions a PostgreSQL certificate and a separate app password. [`db/`](../db/) requires TLS 1.3 for network connections and sets up a non-owner application role with explicit table, column, and sequence grants. The client uses `verify-full`. On the live stack, PostgreSQL reported TLS 1.3 and a role without superuser, database-creation, or role-creation rights. |
| 5 | A failed team edit could save the name but not the slots, and concurrent edits could read stale slot IDs. | The teams service uses a single persistence operation that locks the parent team, verifies member ownership, then changes the name and replaces slots in one transaction. A live test checks that two concurrent edits each return matching names and members and that the final state matches one complete edit. |
| 6 | Losing a response after profile creation could delete the credential but leave the unique username reserved. | Auth persists a pending signup and reuses its player ID when retried with the same credentials. Profile creation acknowledges a matching existing record without duplicating starters; pending credentials cannot log in. The migration marks existing credentials complete. A unit test simulates a lost response and checks safe retry; a live migration rerun preserved an existing test account and starter collection. |
| 7 | Malformed JSON could reach the database, mistyped filters were silently ignored, integer lists were truncated, and some allowlisted columns did not exist. The API spec omitted `/count` and described every batch ID as an integer. | Validate request objects, operation arguments, row IDs, filter names and membership limits before checkout. Use the real per-table timestamp columns and reject unsupported filters. Update [OpenAPI](openapi/db-manager.yaml) for filters, `/count`, UUID IDs, database errors, and the team write. |
| 8 | The service could appear ready even when it could not use PostgreSQL, and database waits had no explicit bounds. | Compose now probes loopback-only `/ready`, which executes `SELECT 1`. The pool sets a 3-second connect timeout, 5-second statement timeout, and 3-second lock timeout. A fresh stack reached readiness with the restricted role. |
| 9 | Existing tests did not verify rollback and column permissions at the database API boundary. | [Live tests](../tests/integration/test_service_identity_live.py) use the real service identity and PostgreSQL. They verify that a later constraint failure rolls back the first write, that profile column restrictions apply to both write routes, and that logout survives a gateway restart. The test Compose override publishes `db-manager` on loopback only. |

## Upgrade an existing Compose deployment

Fresh deployments provision the role and schema automatically. PostgreSQL does not run first-start scripts again for an existing volume. Back up **both** `db_data` and `runtime` together first, following [Backup and recovery](security.md#backup-and-recovery). Keep the same Compose project name, database name, and bootstrap user. `monster_tamers_app` is reserved for the application role.

```sh
docker compose down                              # Do not pass -v.
docker compose up -d --build --wait db             # Runs init and starts PostgreSQL.
docker compose exec -T db /docker-entrypoint-initdb.d/10-provision-app.sh
docker compose up -d --build --wait
```

The provisioning script is safe to rerun. It adds the registration-status column if missing and grants access to the restricted role without rewriting existing credential hashes or progress. Provisioning removes the old bootstrap password copy from the `db-manager` secret directory; it does not rotate the database's bootstrap password.

## Verification and limits

- **104 unit tests passed.** Ruff, Bandit, Compose configuration, YAML parsing, and `git diff --check` passed.
- **20 targeted integration tests passed** against a fresh, disposable Compose deployment. These include the 12-parallel-encounter check, concurrent team edits, logout across a gateway restart, and PostgreSQL-backed rollback and column checks. The migration was also rerun on that populated deployment and an existing test account could still log in.
- The rest of the live suite, the performance suite, long-duration revocation growth, and an upgrade of an actual deployed volume were not checked. New team creation still uses a compensating delete; the atomic write here covers team edits. Teams reads still fetch team and slots separately.
- Additional compatibility observation: the provisioned CA chain worked with the project's Python 3.12 containers and CA-verifying `curl`, but a strict Python 3.13 HTTPS client rejected the leaf certificate for a missing Authority Key Identifier. Existing certificate rotation was not changed as part of these fixes.
