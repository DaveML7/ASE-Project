"""Live check that every microservice role can only touch its own schema (needs docker compose)."""

import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.test.yml"]


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("GATEWAY_URL"), reason="stack not running"),
]


def psql(role: str, sql: str) -> subprocess.CompletedProcess:
    script = f"SET ROLE svc_{role}; {sql}"
    return subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "$0"',
            script,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture(scope="module", autouse=True)
def compose_available():
    probe = subprocess.run([*COMPOSE, "ps", "db"], cwd=ROOT, capture_output=True, timeout=60)
    if probe.returncode != 0:
        pytest.skip("docker compose is not available")


@pytest.mark.parametrize(
    "role, table",
    [
        ("auth", "players_data.players"),
        ("players", "auth_data.accounts"),
        ("teams", "players_data.players"),
        ("encounters", "auth_data.accounts"),
        ("monsters", "teams_data.teams"),
        ("history", "auth_data.accounts"),
    ],
)
def test_a_service_role_cannot_read_another_services_tables(role, table):
    result = psql(role, f"SELECT 1 FROM {table} LIMIT 1;")
    assert result.returncode != 0
    assert "permission denied" in result.stderr


def test_a_service_role_cannot_create_objects_or_change_grants():
    for statement in ("CREATE TABLE public.x (a int);", "DROP TABLE monsters_data.monsters;"):
        assert psql("players", statement).returncode != 0


def test_every_service_can_reach_its_own_schema():
    assert psql("monsters", "SELECT count(*) FROM monsters_data.monsters;").returncode == 0


def test_expired_revocations_are_purged_by_the_next_logout():
    """The revocation list must not grow for ever: expired entries are removed on logout."""
    import uuid

    import requests
    import urllib3

    urllib3.disable_warnings()
    gateway = os.environ["GATEWAY_URL"].rstrip("/")
    session = requests.Session()
    session.verify = False
    session.trust_env = False
    jti = "expired-" + uuid.uuid4().hex

    def admin(sql: str) -> str:
        done = subprocess.run(
            [
                *COMPOSE,
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$0"',
                sql,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    admin(
        "INSERT INTO auth_data.revoked_tokens (jti, player_id, expires_at) "
        f"VALUES ('{jti}', '{uuid.uuid4()}', now() - interval '1 hour')"
    )
    name = "pg_" + uuid.uuid4().hex[:10]
    token = session.post(
        f"{gateway}/auth/register", json={"username": name, "password": "Passw0rd!x"}
    ).json()["access_token"]
    session.post(f"{gateway}/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert admin(f"SELECT count(*) FROM auth_data.revoked_tokens WHERE jti = '{jti}'") == "0"
