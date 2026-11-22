"""Teams service: ownership, validation and limits, with an in-memory database."""

import importlib.util
import pathlib

import pytest
from ase_common import issue_token

SERVICE = pathlib.Path(__file__).parents[2] / "services" / "teams" / "app.py"
ALICE, BOB = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeDb:
    def __init__(self):
        self.teams, self.slots, self.next_id = {}, {}, 1
        self.fail_slots = False
        self.fail_delete = False

    def count(self, table, **filters):
        return sum(t["player_id"] == filters["player_id"] for t in self.teams.values())

    def select(self, table, **filters):
        if table == "teams":
            return [t for t in self.teams.values() if t["player_id"] == filters["player_id"]]
        wanted = set(filters["team_id__in"])
        rows = [
            {"team_id": team, "slot": i, "player_monster_id": m}
            for team, members in self.slots.items()
            if team in wanted
            for i, m in enumerate(members, 1)
        ]
        return sorted(rows, key=lambda r: r["slot"])

    def get(self, table, row_id):
        return self.teams.get(row_id)

    def insert(self, table, values):
        team = {"id": self.next_id, **values}
        self.teams[self.next_id] = team
        self.next_id += 1
        return team

    def replace_team_slots(self, team_id, member_ids, name=None):
        if self.fail_slots:
            raise RuntimeError("database down")
        self.slots[team_id] = list(member_ids)
        if name:
            self.teams[team_id]["name"] = name
        team = self.teams[team_id]
        return {"id": team_id, "name": team["name"], "member_ids": list(member_ids)}

    def delete(self, table, row_id):
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.teams.pop(row_id, None)
        self.slots.pop(row_id, None)


@pytest.fixture
def teams(key_dir, monkeypatch):
    spec = importlib.util.spec_from_file_location("teams_app", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = FakeDb()
    monkeypatch.setattr(module, "db", db)
    monkeypatch.setattr(
        module,
        "call_service",
        lambda *a, **k: FakeResponse([{"player_monster_id": n} for n in (1, 2, 3)]),
    )
    module.fake = db
    return module


def headers(player):
    return {"Authorization": f"Bearer {issue_token(player, 'p')}"}


def create(client, player=ALICE, **body):
    body.setdefault("member_ids", [1, 2])
    return client.post("/teams", json=body, headers=headers(player))


def test_create_list_update_delete_round_trip(teams):
    client = teams.app.test_client()
    created = create(client, name="  Alpha ")
    assert created.status_code == 201 and created.json["name"] == "Alpha"
    assert created.json["max_size"] == 3
    team_id = created.json["id"]
    assert client.get("/teams", headers=headers(ALICE)).json[0]["member_ids"] == [1, 2]
    updated = client.put(
        f"/teams/{team_id}", json={"member_ids": [3], "name": "Beta"}, headers=headers(ALICE)
    )
    assert updated.status_code == 200 and updated.json["member_ids"] == [3]
    assert client.delete(f"/teams/{team_id}", headers=headers(ALICE)).status_code == 204
    assert client.get(f"/teams/{team_id}", headers=headers(ALICE)).status_code == 404


@pytest.mark.parametrize(
    "body, status",
    [
        ({"member_ids": "no"}, 400),
        ({"member_ids": [1, True]}, 400),
        ({"member_ids": []}, 422),
        ({"member_ids": [1, 1]}, 422),
        ({"member_ids": [1, 2, 3, 4]}, 422),
        ({"member_ids": [99]}, 422),
        ({"member_ids": [1], "name": {"a": 1}}, 400),
        ({"member_ids": [1], "name": "   "}, 400),
        ({"member_ids": [1], "name": ["x"]}, 400),
    ],
)
def test_invalid_teams_are_refused_and_nothing_is_stored(teams, body, status):
    assert (
        teams.app.test_client().post("/teams", json=body, headers=headers(ALICE)).status_code
        == status
    )
    assert teams.fake.teams == {}


def test_name_is_trimmed_to_the_limit_and_defaults(teams):
    client = teams.app.test_client()
    assert create(client, name="x" * 100).json["name"] == "x" * 40
    assert create(client).json["name"] == "Team"


def test_a_player_cannot_exceed_the_team_limit(teams):
    from team_rules import TEAMS_PER_PLAYER

    client = teams.app.test_client()
    assert all(create(client).status_code == 201 for _ in range(TEAMS_PER_PLAYER))
    refused = create(client)
    assert refused.status_code == 409 and "delete one" in refused.json["message"]
    assert len(client.get("/teams", headers=headers(ALICE)).json) == TEAMS_PER_PLAYER
    assert create(client, player=BOB).status_code == 201  # the limit is per player


def test_other_players_cannot_read_change_validate_or_delete_a_team(teams):
    client = teams.app.test_client()
    team_id = create(client).json["id"]
    mallory = headers(BOB)
    assert client.get(f"/teams/{team_id}", headers=mallory).status_code == 404
    assert (
        client.put(f"/teams/{team_id}", json={"member_ids": [1]}, headers=mallory).status_code
        == 404
    )
    assert client.post(f"/teams/{team_id}/validate", headers=mallory).status_code == 404
    assert client.delete(f"/teams/{team_id}", headers=mallory).status_code == 404
    assert client.get("/teams", headers=mallory).json == []


def test_validate_reports_a_valid_team(teams):
    client = teams.app.test_client()
    team_id = create(client).json["id"]
    body = client.post(f"/teams/{team_id}/validate", headers=headers(ALICE)).json
    assert body["valid"] is True and body["violations"] == []


def test_failed_creation_removes_the_half_created_team_and_reports_the_real_error(teams):
    teams.fake.fail_slots = True
    client = teams.app.test_client()
    client.application.testing = False
    assert create(client).status_code == 500
    assert teams.fake.teams == {}


def test_a_failing_cleanup_does_not_hide_the_original_error(teams):
    teams.fake.fail_slots = teams.fake.fail_delete = True
    client = teams.app.test_client()
    response = create(client)
    assert response.status_code == 500 and response.json["message"] == "unexpected error"


def test_requires_a_token(teams):
    client = teams.app.test_client()
    assert client.get("/teams").status_code == 401
    assert client.post("/teams", json={"member_ids": [1]}).status_code == 401
