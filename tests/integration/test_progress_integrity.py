"""Integration tests against the running stack, through the gateway only.

    docker compose up -d --wait
    GATEWAY_URL=https://localhost:8443/api/v1 python -m pytest tests/integration

Covers the "protected progress" stories: concurrent encounters must neither lose
XP nor duplicate rewards (US-25), and saved progress must survive a new login
(US-03). Skipped automatically when GATEWAY_URL is not set.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
import urllib3

GATEWAY_URL = os.getenv("GATEWAY_URL", "").rstrip("/")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not GATEWAY_URL, reason="GATEWAY_URL not set (stack not running)"),
]
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # self-signed certificates

XP_WIN, XP_LOSS = 20, 5
PARALLEL = 12


@pytest.fixture(scope="module")
def http():
    session = requests.Session()
    session.trust_env = False  # ignore proxy / CA bundle environment variables
    session.verify = False  # self-signed CA; the Newman runs do the same
    return session


def _register(http):
    name = f"it_{uuid.uuid4().hex[:12]}"
    response = http.post(
        f"{GATEWAY_URL}/auth/register", json={"username": name, "password": "integration1"}
    )
    assert response.status_code == 201, response.text
    return name, {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def player(http):
    name, headers = _register(http)
    collection = http.get(f"{GATEWAY_URL}/players/me/monsters", headers=headers).json()
    team = http.post(
        f"{GATEWAY_URL}/teams",
        headers=headers,
        json={"name": "it", "member_ids": [m["player_monster_id"] for m in collection]},
    )
    assert team.status_code == 201, team.text
    return {"name": name, "headers": headers, "team_id": team.json()["id"], "starters": len(collection)}


def test_concurrent_encounters_keep_progress_consistent(http, player):
    def fight(_):
        return http.post(
            f"{GATEWAY_URL}/encounters",
            headers=player["headers"],
            json={"team_id": player["team_id"], "template_id": 1},
        )

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        responses = list(pool.map(fight, range(PARALLEL)))

    assert [r.status_code for r in responses] == [201] * PARALLEL, [r.text for r in responses]
    wins = sum(r.json()["outcome"] == "win" for r in responses)
    losses = PARALLEL - wins
    expected_xp = wins * XP_WIN + losses * XP_LOSS

    profile = http.get(f"{GATEWAY_URL}/players/me", headers=player["headers"]).json()
    assert profile["xp"] == expected_xp, "an XP update was lost under concurrency"
    assert profile["level"] == 1 + expected_xp // 100

    history = http.get(f"{GATEWAY_URL}/history", headers=player["headers"]).json()
    assert len(history) == PARALLEL
    created = [entry["created_at"] for entry in history]
    assert created == sorted(created, reverse=True), "history must be newest first"

    collection = http.get(f"{GATEWAY_URL}/players/me/monsters", headers=player["headers"]).json()
    monster_ids = [m["monster_id"] for m in collection]
    assert len(monster_ids) == len(set(monster_ids)), "a reward was granted twice"
    rewards = sum(1 for r in responses if r.json()["outcome"] == "win" and r.json()["details"].get("reward"))
    assert len(collection) == player["starters"] + rewards, "reward and history disagree"


def test_progress_survives_a_new_login(http, player):
    before = http.get(f"{GATEWAY_URL}/players/me", headers=player["headers"]).json()
    login = http.post(
        f"{GATEWAY_URL}/auth/login", json={"username": player["name"], "password": "integration1"}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    after = http.get(f"{GATEWAY_URL}/players/me", headers=headers).json()
    assert (after["xp"], after["level"], after["monsters_owned"]) == (
        before["xp"], before["level"], before["monsters_owned"],
    )


def test_a_player_cannot_use_or_read_another_players_data(http, player):
    _, intruder = _register(http)
    stolen = http.post(
        f"{GATEWAY_URL}/encounters", headers=intruder, json={"team_id": player["team_id"], "template_id": 1}
    )
    assert stolen.status_code == 403
    assert http.get(f"{GATEWAY_URL}/teams/{player['team_id']}", headers=intruder).status_code == 404
    assert http.get(f"{GATEWAY_URL}/history", headers=intruder).json() == []
