"""Live failure test: the database layer goes away and comes back.

Stops the db-manager container, checks that the public catalogue is still served from the
cache, that protected calls fail FAST with a retryable 503/502 (circuit breaker) instead of
hanging, and that everything recovers by itself. Needs docker compose and the test override.
"""

import os
import pathlib
import subprocess
import time
import uuid

import pytest
import requests
import urllib3

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.test.yml"]
GATEWAY = os.getenv("GATEWAY_URL", "").rstrip("/")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not GATEWAY, reason="GATEWAY_URL not set (stack not running)"),
]
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def compose(*args):
    return subprocess.run([*COMPOSE, *args], cwd=ROOT, capture_output=True, text=True, timeout=120)


@pytest.fixture(scope="module")
def http():
    session = requests.Session()
    session.trust_env = False
    session.verify = False
    return session


@pytest.fixture
def database_layer_down(http):
    if compose("ps", "db-manager").returncode != 0:
        pytest.skip("docker compose is not available")
    # warm the catalogue cache of the monsters service while everything is healthy; every
    # gunicorn worker keeps its own cache, so ask often enough to reach all of them
    assert all(http.get(f"{GATEWAY}/monsters").status_code == 200 for _ in range(30))
    compose("stop", "db-manager")
    try:
        yield
    finally:
        compose("start", "db-manager")
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                if http.get(f"{GATEWAY}/monsters/1").status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(2)


def test_failures_are_fast_and_the_system_recovers(http, database_layer_down):
    name = f"rs_{uuid.uuid4().hex[:12]}"
    # public, cached data keeps being served while the database layer is down
    assert http.get(f"{GATEWAY}/monsters").status_code == 200

    # protected calls fail with a retryable error, and after a few failures instantly
    durations, codes = [], []
    for _ in range(12):
        started = time.perf_counter()
        response = http.post(
            f"{GATEWAY}/auth/register", json={"username": name, "password": "resilience1"}
        )
        durations.append(time.perf_counter() - started)
        codes.append(response.status_code)
    assert set(codes) <= {502, 503}, codes
    assert all(code in (502, 503) for code in codes)
    assert max(durations[-4:]) < 1.5, f"the open circuit must fail fast, took {durations[-4:]}"
    assert 503 in codes, "the circuit breaker must open and answer 503 + Retry-After"
    refused = http.post(
        f"{GATEWAY}/auth/register", json={"username": name, "password": "resilience1"}
    )
    assert refused.status_code == 503 and int(refused.headers["Retry-After"]) >= 1


def test_everything_works_again_after_the_restart(http):
    deadline, status = time.time() + 90, None
    name = f"rs_{uuid.uuid4().hex[:12]}"
    while time.time() < deadline:
        response = http.post(
            f"{GATEWAY}/auth/register", json={"username": name, "password": "resilience1"}
        )
        status = response.status_code
        if status == 201:
            break
        time.sleep(2)
    assert status == 201, "the services must recover without a restart"
