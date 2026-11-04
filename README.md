# Monster Tamers

Backend for the ASE 2026/2027 "Monster Tamer" project: a microservice game backend
(API gateway + 7 services + PostgreSQL) plus a minimal browser client.

## Get started

Prerequisites: Docker (with the Compose plugin) and, for the frontend and tests,
Python 3.12+.

```bash
git clone <repository-url>
cd ASE_Project

# 1. Configure secrets (defaults work for local development)
cp .env.example .env

# 2. Build and start the whole backend
docker compose up

# 3. In another terminal, check it is alive
curl -k https://localhost:8443/health
```

`docker compose up` builds every image, generates the self-signed certificates
inside the image build, applies `db/init.sql` on the first start and exposes only
the gateway on `https://localhost:8443`. Recreate the database from scratch with
`docker compose down -v`.

### Minimal API walkthrough

```bash
API=https://localhost:8443/api/v1

# register -> returns a JWT and creates the starter collection
TOKEN=$(curl -ks -X POST $API/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"ash","password":"pikachu123"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

# browse the catalogue (public) and your own monsters
curl -ks $API/monsters | head -c 400
curl -ks -H "Authorization: Bearer $TOKEN" $API/players/me/monsters

# create a team from the first three owned monsters
MINE=$(curl -ks -H "Authorization: Bearer $TOKEN" $API/players/me/monsters \
  | python3 -c 'import json,sys;print([m["player_monster_id"] for m in json.load(sys.stdin)][:3])')
curl -ks -X POST $API/teams -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d "{\"name\":\"Starter squad\",\"member_ids\":$MINE}"

# start an encounter, then read the history
curl -ks -X POST $API/encounters -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"team_id":1,"template_id":1}'
curl -ks -H "Authorization: Bearer $TOKEN" $API/history
```

Log in again with `POST /auth/login` whenever the token expires (default 1 hour).

## Frontend (outside Docker)

The client is static HTML/JS and must run outside the backend Docker network.

```bash
cd frontend
python3 -m http.server 8000
# open http://localhost:8000
```

Before using the UI, open <https://localhost:8443/health> once and accept the
self-signed certificate, otherwise the browser blocks the API calls. The frontend
talks only to `https://localhost:8443/api/v1`; no business logic lives in it.

## Tests

```bash
# unit tests for the game rules (team composition, encounter resolution)
pip install -r requirements-dev.txt
pip install ./common
pytest

# component tests for the internal services (Bruno)
#   import docs/bruno/*.json, base URLs pointed at the service ports
# performance tests against the gateway (Locust)
pip install -r tests/locust/requirements.txt
locust -f tests/locust/locustfile.py --host https://localhost:8443
```

The services are reachable for component testing with
`docker compose exec <service> ...` or by temporarily publishing a service port;
integration runs reuse the same Bruno requests against the gateway by changing
the base URL to `https://localhost:8443/api/v1`.

## Architecture

```
                        browser (frontend/, http://localhost:8000)
                                        |
                                        | HTTPS (self-signed CA)
                                        v
                                   +---------+
                                   | gateway |  :8443  JWT validation, CORS, routing
                                   +----+----+  (only published port)
              +-----------------+-------+--------+------------------+
              v                 v                v                  v
         +---------+      +---------+       +---------+       +----------+
         |  auth   |      | players |       | monsters|  ...  | history  |
         |  :5001  |      |  :5002  |       |  :5003  |       |  :5006   |
         +----+----+      +----+----+       +----+----+       +----+-----+
              |                |                 |                 |
              +----------------+--------+--------+-----------------+
                                        v
                                 +---------------+
                                 |  db-manager   |  :5010  allowlisted CRUD + transactions
                                 +-------+-------+
                                         v
                                   +----------+
                                   | postgres |  :5432 (internal only)
                                   +----------+
```

* `gateway` – single HTTPS entry point; validates the JWT and forwards requests. No business logic.
* `auth` – registration, login, JWT issuing (HS256), password hashing.
* `players` – profile, level/XP, personal collection, starter monsters.
* `monsters` – public catalogue and generated SVG artwork.
* `teams` – team creation/modification and the team rules.
* `encounters` – encounter templates, resolution, rewards and progress updates.
* `history` – read-only view of past encounters.
* `db-manager` – the only component that talks SQL; validates table/column names and
  offers a transactional batch endpoint. Domain services never touch PostgreSQL directly.
* `postgres` – single data store created by `db/init.sql`.

Service-to-service calls carry a shared `X-Internal-Key` and use HTTPS with the
CA in `/certs`. More design notes: [docs/architecture.md](docs/architecture.md).

## API overview

All routes are behind `https://localhost:8443/api/v1`. Bearer token required unless marked public.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/auth/register` | Create account, returns JWT (public) |
| POST | `/auth/login` | Log in, returns JWT (public) |
| GET | `/players/me` | Profile and progress |
| PATCH | `/players/me` | Update avatar/faction |
| GET | `/players/me/monsters` | Personal collection |
| GET | `/monsters`, `/monsters/{id}` | Catalogue and details (public) |
| GET | `/monsters/{id}/image` | Monster artwork (public) |
| GET, POST | `/teams` | List / create teams |
| GET, PUT, DELETE | `/teams/{id}` | Read / modify / delete a team |
| POST | `/teams/{id}/validate` | Validate a stored team |
| GET, POST | `/encounters` | Encounter list / start and resolve |
| GET | `/encounters/{id}` | Encounter details |
| GET | `/history`, `/history/{id}` | Encounter history |

Full specifications: [docs/openapi/](docs/openapi) (`gateway.yaml` for the public API,
one file per internal microservice).

## Repository layout

```
common/            shared Flask factory, JWT helpers, db-manager client
services/          one folder per microservice (app.py + rules + unit tests)
docker/Dockerfile  single parameterised image (build arg SERVICE)
certs/             self-signed certificate generator (runs at image build)
db/init.sql        schema and seed data
frontend/          static client, runs outside Docker
docs/              OpenAPI, Bruno, Locust notes, report material, copied workflow
docs/requirements/ the course PDF and backlog spreadsheet this project implements
tests/locust/      performance tests
```

## Security

* All containers run as the non-root user `appuser` (uid 10001).
* Every hop is HTTPS with self-signed certificates from a project-local CA.
* Passwords are stored as scrypt hashes; no credential is ever returned by an API.
* JWTs are HS256-signed, expire after `JWT_TTL_SECONDS` and are re-validated by
  each service; every resource lookup is scoped to the token subject.
* Inputs are validated and length-limited per endpoint; `db-manager` rejects
  unknown tables/columns and only ever uses parameterised statements.
* `db-manager` and the `/internal/*` endpoints require `X-Internal-Key`.
* CI runs ruff, bandit, pip-audit and the unit tests, then builds the full stack
  and smoke-tests the gateway.

## Project backlog status

Initial scaffold covers US-01, US-02, US-03, US-06, US-09, US-10, US-11, US-12,
US-14, US-15, US-16, US-17, US-18, US-19, US-20, US-23, US-24 and US-28, plus the
avatar/faction part of US-04. Not implemented yet: US-07, US-08, US-13, US-21,
US-22, US-26, US-27; US-05 and US-25 are partially covered (hashing, JWT, scoping).

Acceptance criteria and dependencies still have to be written by the group — start
from [docs/backlog-template.md](docs/backlog-template.md).
