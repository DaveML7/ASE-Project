"""History presents partial snapshots and scopes lookups to the signed-in player."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
from ase_common import issue_token

SERVICE = Path(__file__).parents[2] / "services" / "history" / "app.py"
PLAYER = "11111111-1111-1111-1111-111111111111"


def row(encounter_id, details):
    return {
        "id": encounter_id,
        "template_id": 1,
        "wild_monster_id": 2,
        "outcome": "loss",
        "created_at": "Sat, 21 Nov 2026 12:00:00 GMT",
        "details": details,
    }


@pytest.fixture
def history(key_dir, monkeypatch):
    spec = importlib.util.spec_from_file_location("history_app", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = Mock()
    monkeypatch.setattr(module, "db", db)
    return module.app.test_client(), db, {"Authorization": f"Bearer {issue_token(PLAYER, 'ash')}"}


def test_history_skips_invalid_summary_fields_without_losing_the_row(history):
    client, db, headers = history
    db.encounter_history.return_value = [
        row(2, {"team": [None, {"name": "Ash"}, {"name": 10}], "wild_monster": "legacy"}),
        row(1, []),
    ]
    response = client.get("/history", headers=headers)
    assert response.status_code == 200
    assert [item["id"] for item in response.json] == [2, 1]
    assert response.json[0]["summary"] == {
        "team": ["Ash"],
        "wild_monster": None,
        "reward": None,
        "template": None,
    }
    assert response.json[1]["summary"]["team"] == []
    assert "details" not in response.json[0]
    db.encounter_history.assert_called_once_with(PLAYER, 51, None)


def test_history_links_to_next_page_without_changing_array_contract(history):
    client, db, headers = history
    db.encounter_history.return_value = [row(2, {}), row(1, {})]
    response = client.get("/history?limit=1", headers=headers)
    assert [item["id"] for item in response.json] == [2]
    assert response.headers["Link"] == '<?limit=1&before=2>; rel="next"'
    db.encounter_history.return_value = [row(1, {})]
    response = client.get("/history?limit=1&before=2", headers=headers)
    assert response.json[0]["id"] == 1
    assert "Link" not in response.headers
    db.encounter_history.assert_called_with(PLAYER, 2, 2)


def test_detail_filters_by_both_player_and_encounter_and_keeps_the_snapshot(history):
    client, db, headers = history
    snapshot = {"team": [{"name": "Ash", "level": 2}], "resolution": {"score": 42}}
    db.select.return_value = [row(5, snapshot)]
    response = client.get("/history/5", headers=headers)
    assert response.json["details"] == snapshot
    db.select.assert_called_once_with("encounters", player_id=PLAYER, id=5, limit=1)
    db.select.return_value = []
    assert client.get("/history/6", headers=headers).status_code == 404


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/history?limit=oops", 400),
        ("/history?before=0", 400),
        ("/history?before=9223372036854775808", 400),
        ("/history/9223372036854775808", 404),
    ],
)
def test_invalid_input_is_rejected_before_hitting_persistence(history, path, status):
    client, db, headers = history
    assert client.get(path, headers=headers).status_code == status
    assert db.mock_calls == []
