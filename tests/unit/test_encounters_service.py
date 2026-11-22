"""Encounter service: ownership, atomic persistence and the reward retry.

The persistence layer and the peer services are replaced by in-memory fakes, so
the real request handler runs without Docker.
"""

import importlib.util
import pathlib

import pytest
from ase_common import DbError

SERVICE = pathlib.Path(__file__).parents[2] / "services" / "encounters" / "app.py"
PLAYER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def module(key_dir):
    spec = importlib.util.spec_from_file_location("encounters_app", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        assert self.status_code < 400


class FakeDb:
    """Just enough of the db-manager client for one encounter."""

    def __init__(self, batch_errors=()):
        self.batches = []
        self.batch_errors = list(batch_errors)
        self.monsters = [
            {
                "id": 1,
                "name": "A",
                "element": "fire",
                "hp": 40,
                "attack": 10,
                "defence": 5,
                "rarity": "common",
                "description": "",
            },
            {
                "id": 2,
                "name": "B",
                "element": "water",
                "hp": 40,
                "attack": 10,
                "defence": 5,
                "rarity": "common",
                "description": "",
            },
            {
                "id": 3,
                "name": "Wild",
                "element": "air",
                "hp": 1,
                "attack": 1,
                "defence": 0,
                "rarity": "common",
                "description": "",
            },
        ]

    def get(self, table, row_id):
        if table == "encounter_templates":
            return (
                {
                    "id": row_id,
                    "name": "T",
                    "difficulty": 1,
                    "description": "",
                    "wild_monster_id": 3,
                }
                if row_id == 1
                else None
            )
        if table == "monsters":
            return next(m for m in self.monsters if m["id"] == row_id)
        if table == "players":
            return {"id": PLAYER, "xp": 0, "level": 1}
        return None

    def select(self, table, **_):
        return self.monsters

    def catalogue(self):
        return self.monsters

    def batch(self, operations):
        self.batches.append(operations)
        if self.batch_errors:
            raise self.batch_errors.pop(0)
        return [
            {
                "id": 7,
                "template_id": 1,
                "team_id": 5,
                "wild_monster_id": 3,
                "outcome": operations[0]["values"]["outcome"],
                "details": operations[0]["values"]["details"],
            },
            *({"id": 99} for _ in operations[1:-1]),
            {"xp": 20, "level": 1},
        ]


def _client(module, monkeypatch, db, team_owner=PLAYER):
    from ase_common import issue_token

    owned = [
        {
            "player_monster_id": 1,
            "monster_id": 1,
            "name": "A",
            "element": "fire",
            "level": 50,
            "attack": 10,
            "defence": 5,
            "hp": 40,
        }
    ]

    def fake_call(service, method, path, **_):
        if service == "teams":
            return FakeResponse({"id": 5, "player_id": team_owner, "member_ids": [1]})
        return FakeResponse(owned)

    monkeypatch.setattr(module, "db", db)
    monkeypatch.setattr(module, "call_service", fake_call)
    client = module.app.test_client()
    return client, {"Authorization": f"Bearer {issue_token(PLAYER, 'ash')}"}


def test_win_is_persisted_atomically_with_an_increment(module, monkeypatch):
    db = FakeDb()
    client, headers = _client(module, monkeypatch, db)
    response = client.post("/encounters", json={"team_id": 5, "template_id": 1}, headers=headers)
    assert response.status_code == 201
    assert response.json["outcome"] == "win"
    assert response.json["progress"]["xp_gained"] == 20

    assert len(db.batches) == 1, "encounter, reward and xp must share one transaction"
    kinds = [(op["op"], op["table"]) for op in db.batches[0]]
    assert kinds[0] == ("insert", "encounters")
    assert kinds[-1] == ("increment", "players")
    assert db.batches[0][-1]["by"] == 20, "xp must be incremented, not overwritten"


def reward_conflict():
    return DbError(
        "conflict", status_code=409, constraint="player_monsters_player_id_monster_id_key"
    )


def test_reward_conflict_is_retried_and_succeeds(module, monkeypatch):
    db = FakeDb(batch_errors=[reward_conflict()])
    client, headers = _client(module, monkeypatch, db)
    response = client.post("/encounters", json={"team_id": 5, "template_id": 1}, headers=headers)
    assert response.status_code == 201
    assert len(db.batches) == 2


def test_persistent_conflicts_give_up(module, monkeypatch):
    db = FakeDb(batch_errors=[reward_conflict()] * 3)
    client, headers = _client(module, monkeypatch, db)
    response = client.post("/encounters", json={"team_id": 5, "template_id": 1}, headers=headers)
    assert response.status_code == 502
    assert len(db.batches) == module.MAX_ATTEMPTS


def test_unrelated_conflict_is_not_retried(module, monkeypatch):
    db = FakeDb(
        batch_errors=[DbError("conflict", status_code=409, constraint="encounters_outcome_check")]
    )
    client, headers = _client(module, monkeypatch, db)
    assert (
        client.post(
            "/encounters", json={"team_id": 5, "template_id": 1}, headers=headers
        ).status_code
        == 502
    )
    assert len(db.batches) == 1


def test_other_players_team_is_forbidden_and_nothing_is_written(module, monkeypatch):
    db = FakeDb()
    client, headers = _client(
        module, monkeypatch, db, team_owner="22222222-2222-2222-2222-222222222222"
    )
    response = client.post("/encounters", json={"team_id": 5, "template_id": 1}, headers=headers)
    assert response.status_code == 403
    assert db.batches == []


@pytest.mark.parametrize(
    "body", [{}, {"team_id": "5", "template_id": 1}, {"team_id": True, "template_id": 1}]
)
def test_malformed_body_is_rejected(module, monkeypatch, body):
    client, headers = _client(module, monkeypatch, FakeDb())
    assert client.post("/encounters", json=body, headers=headers).status_code == 400


def test_requires_a_token(module, monkeypatch):
    client, _ = _client(module, monkeypatch, FakeDb())
    assert client.post("/encounters", json={"team_id": 5, "template_id": 1}).status_code == 401


def test_a_win_pays_a_reward_only_when_the_chance_roll_succeeds(module, monkeypatch):
    for chance, expected in ((1.0, True), (0.0, False)):
        monkeypatch.setattr(module, "REWARD_CHANCE", chance)
        db = FakeDb()
        client, headers = _client(module, monkeypatch, db)
        response = client.post(
            "/encounters", json={"team_id": 5, "template_id": 1}, headers=headers
        )
        assert response.status_code == 201 and response.json["outcome"] == "win"
        tables = [op["table"] for op in db.batches[0]]
        assert ("player_monsters" in tables) is expected


def test_a_team_with_a_monster_the_player_no_longer_owns_is_refused(module, monkeypatch):
    db = FakeDb()
    client, headers = _client(module, monkeypatch, db)
    original = module.call_service

    def team_with_a_stranger(service, method, path, **kwargs):
        response = original(service, method, path, **kwargs)
        if service == "teams":
            response._payload["member_ids"] = [1, 999]
        return response

    monkeypatch.setattr(module, "call_service", team_with_a_stranger)
    response = client.post("/encounters", json={"team_id": 5, "template_id": 1}, headers=headers)
    assert response.status_code == 422 and db.batches == []


def test_unknown_template_is_reported_as_a_missing_template(module, monkeypatch):
    client, headers = _client(module, monkeypatch, FakeDb())
    response = client.post("/encounters", json={"team_id": 5, "template_id": 99}, headers=headers)
    assert response.status_code == 404 and "template" in response.json["message"]
