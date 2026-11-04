"""Locust performance tests for the Monster Tamers core loop.

Targets the API gateway only, as required by the project specification.

    pip install -r tests/locust/requirements.txt
    locust -f tests/locust/locustfile.py --host https://localhost:8443

Open http://localhost:8089 to drive the load. The gateway uses a self-signed
certificate, so TLS verification is disabled for the load generator only.
"""

import uuid

from locust import HttpUser, between, task


class PlayerUser(HttpUser):
    """Registers once, then plays the prepare -> encounter -> history loop."""

    wait_time = between(1, 3)

    def on_start(self):
        self.client.verify = False  # shortcut: self-signed cert; mount certs/ca.crt for real verification
        self.username = f"perf_{uuid.uuid4().hex[:12]}"
        self.headers = {}
        self.team_id = None
        self.collection = []

        response = self.client.post(
            "/api/v1/auth/register",
            json={"username": self.username, "password": "performance1"},
            name="POST /auth/register",
        )
        if response.status_code >= 400:
            return
        self.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        collection = self.client.get(
            "/api/v1/players/me/monsters", headers=self.headers, name="GET /players/me/monsters"
        )
        if collection.status_code == 200:
            self.collection = [entry["player_monster_id"] for entry in collection.json()]

    @task(2)
    def browse_catalogue(self):
        self.client.get("/api/v1/monsters", name="GET /monsters")

    @task(1)
    def read_profile(self):
        if self.headers:
            self.client.get("/api/v1/players/me", headers=self.headers, name="GET /players/me")

    @task(1)
    def create_team(self):
        if not self.collection:
            return
        response = self.client.post(
            "/api/v1/teams",
            headers=self.headers,
            json={"name": "Load squad", "member_ids": self.collection[:3]},
            name="POST /teams",
        )
        if response.status_code == 201:
            self.team_id = response.json()["id"]

    @task(4)
    def run_encounter(self):
        if not self.team_id:
            return
        encounters = self.client.get(
            "/api/v1/encounters", headers=self.headers, name="GET /encounters"
        )
        if encounters.status_code != 200 or not encounters.json():
            return
        self.client.post(
            "/api/v1/encounters",
            headers=self.headers,
            json={"team_id": self.team_id, "template_id": encounters.json()[0]["id"]},
            name="POST /encounters",
        )

    @task(1)
    def read_history(self):
        if self.headers:
            self.client.get("/api/v1/history", headers=self.headers, name="GET /history")
