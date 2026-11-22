"""Live checks of service identity, logout durability and the persistence boundary.

The test borrows the real private key of a running service (`docker compose exec`) to
sign service tokens, then calls the internal endpoints that the test override publishes
on loopback. It proves who may call what, with real keys and real TLS.
"""

import functools
import os
import pathlib
import subprocess
import time
import uuid

import jwt
import pytest
import requests
import urllib3

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.test.yml"]
GATEWAY = "https://localhost:8443/api/v1"
PLAYERS, TEAMS, AUTH = "https://localhost:5002", "https://localhost:5004", "https://localhost:5001"
DB_MANAGER = "https://localhost:5010"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("GATEWAY_URL"), reason="GATEWAY_URL not set (stack not running)"
    ),
]
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@functools.cache
def private_key(service: str):
    done = subprocess.run(
        [*COMPOSE, "exec", "-T", service, "cat", "/run/app-config/service.key"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if done.returncode != 0:
        pytest.skip(f"cannot read {service} key (stack not running with the test override?)")
    return done.stdout


def service_token(caller: str, audience: str) -> str:
    now = int(time.time())
    claims = {"iss": caller, "aud": audience, "iat": now, "exp": now + 60, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, private_key(caller), algorithm="EdDSA")


@pytest.fixture(scope="module")
def http():
    session = requests.Session()
    session.trust_env = False
    session.verify = False
    return session


def register(http):
    name = f"id_{uuid.uuid4().hex[:12]}"
    body = http.post(
        f"{GATEWAY}/auth/register", json={"username": name, "password": "identity1"}
    ).json()
    return body["player_id"], {"Authorization": f"Bearer {body['access_token']}"}


def collection(http, caller, owner_id, user_headers, audience="players", base=PLAYERS):
    headers = {"X-Service-Token": service_token(caller, audience), **(user_headers or {})}
    return http.get(f"{base}/internal/players/{owner_id}/monsters", headers=headers)


@pytest.fixture(scope="module")
def alice(http):
    return register(http)


@pytest.fixture(scope="module")
def mallory(http):
    return register(http)


@pytest.mark.parametrize("caller", ["encounters", "teams"])
def test_allowed_services_can_read_the_collection_with_the_owners_token(http, alice, caller):
    response = collection(http, caller, alice[0], alice[1])
    assert response.status_code == 200 and len(response.json()) == 3


def test_a_service_cannot_read_someone_elses_collection_even_with_its_own_valid_identity(
    http, alice, mallory
):
    # encounters is allowed to call players, but only with the OWNER's token
    assert collection(http, "encounters", alice[0], mallory[1]).status_code == 403


def test_the_owners_token_is_required(http, alice):
    assert collection(http, "encounters", alice[0], None).status_code == 401


@pytest.mark.parametrize("caller", ["monsters", "history", "gateway"])
def test_services_without_a_granted_public_key_cannot_call_players_internals(http, alice, caller):
    assert collection(http, caller, alice[0], alice[1]).status_code == 403


def test_a_token_meant_for_another_service_is_refused(http, alice):
    assert collection(http, "encounters", alice[0], alice[1], audience="teams").status_code == 403


def test_only_encounters_may_read_internal_teams(http, alice):
    teams = http.post(f"{GATEWAY}/teams", headers=alice[1], json={"name": "t", "member_ids": []})
    assert teams.status_code == 422  # empty team refused; create a real one instead
    owned = http.get(f"{GATEWAY}/players/me/monsters", headers=alice[1]).json()
    team = http.post(
        f"{GATEWAY}/teams",
        headers=alice[1],
        json={"name": "t", "member_ids": [owned[0]["player_monster_id"]]},
    ).json()
    url = f"{TEAMS}/internal/teams/{team['id']}"
    ok = http.get(
        url, headers={"X-Service-Token": service_token("encounters", "teams"), **alice[1]}
    )
    assert ok.status_code == 200 and ok.json()["player_id"] == alice[0]
    for caller in ("players", "monsters", "history"):
        denied = http.get(
            url, headers={"X-Service-Token": service_token(caller, "teams"), **alice[1]}
        )
        assert denied.status_code == 403, caller


def test_only_the_gateway_may_read_the_revocation_list(http):
    url = f"{AUTH}/internal/revoked"
    assert (
        http.get(url, headers={"X-Service-Token": service_token("gateway", "auth")}).status_code
        == 200
    )
    for caller in ("players", "teams", "encounters", "monsters", "history"):
        assert (
            http.get(url, headers={"X-Service-Token": service_token(caller, "auth")}).status_code
            == 403
        )


def test_health_is_not_exposed_to_other_containers_or_hosts(http):
    assert http.get(f"{PLAYERS}/health").status_code == 404  # not loopback inside the container
    assert http.get("https://localhost:8443/health").status_code == 200  # the gateway's is public


def test_logout_really_revokes_the_token_at_the_gateway(http):
    _, headers = register(http)
    assert http.get(f"{GATEWAY}/players/me", headers=headers).status_code == 200
    assert http.post(f"{GATEWAY}/auth/logout", headers=headers).status_code == 204
    assert http.get(f"{GATEWAY}/players/me", headers=headers).status_code == 401


def test_revocation_survives_a_gateway_restart_cycle(http):
    _, headers = register(http)
    token_id = jwt.decode(headers["Authorization"].split()[1], options={"verify_signature": False})[
        "jti"
    ]
    assert http.post(f"{GATEWAY}/auth/logout", headers=headers).status_code == 204
    listed = http.get(
        f"{AUTH}/internal/revoked", headers={"X-Service-Token": service_token("gateway", "auth")}
    )
    assert token_id in listed.json()["jtis"]
    subprocess.run([*COMPOSE, "restart", "gateway"], cwd=ROOT, check=True, timeout=30)
    deadline = time.monotonic() + 10
    while True:
        try:
            response = http.get(f"{GATEWAY}/players/me", headers=headers, timeout=2)
            break
        except requests.RequestException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)
    assert response.status_code == 401


def test_db_manager_rolls_back_the_first_insert_if_the_second_fails(http):
    headers = {"X-Service-Token": service_token("players", "db-manager")}
    player_id = str(uuid.uuid4())
    operation = {
        "op": "insert",
        "table": "players",
        "values": {"id": player_id, "username": f"it_{player_id}"},
    }
    result = http.post(
        f"{DB_MANAGER}/db/batch", headers=headers, json={"operations": [operation, operation]}
    )
    assert result.status_code == 409, result.text
    assert result.json()["constraint"] in {"players_pkey", "players_username_key"}
    assert http.get(f"{DB_MANAGER}/db/players/{player_id}", headers=headers).status_code == 404


def test_db_manager_enforces_profile_columns_on_both_write_routes(http):
    player_id, _ = register(http)
    headers = {"X-Service-Token": service_token("players", "db-manager")}
    profile_url = f"{DB_MANAGER}/db/players/{player_id}"
    assert http.patch(profile_url, headers=headers, json={"avatar": "test"}).status_code == 200
    assert http.patch(profile_url, headers=headers, json={"xp": 99}).status_code == 400
    operation = {"op": "update", "table": "players", "id": player_id, "values": {"xp": 99}}
    assert (
        http.post(
            f"{DB_MANAGER}/db/batch", headers=headers, json={"operations": [operation]}
        ).status_code
        == 400
    )
    profile = http.get(profile_url, headers=headers).json()
    assert (profile["avatar"], profile["xp"]) == ("test", 0)
