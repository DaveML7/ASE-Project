"""Auth retries an uncertain signup and only acknowledges a durable revocation."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from ase_common import DbError, issue_token

SERVICE = Path(__file__).parents[2] / "services" / "auth" / "app.py"
PLAYER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def auth_service(key_dir, monkeypatch):
    spec = importlib.util.spec_from_file_location("auth_app", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "db", Mock())
    return module


@pytest.mark.parametrize(("db_error", "status"), [
    (DbError("duplicate", 409, "revoked_tokens_jti_key"), 204),
    (DbError("other constraint", 409, "revoked_tokens_pkey"), 502),
    (DbError("down", 503), 502),
])
def test_logout_only_ignores_the_expected_duplicate(auth_service, db_error, status):
    auth_service.db.insert.side_effect = db_error
    response = auth_service.app.test_client().post(
        "/auth/logout", headers={"Authorization": f"Bearer {issue_token(PLAYER, 'ash')}"},
    )
    assert response.status_code == status


def test_revocation_list_fetches_all_pages(auth_service):
    first = [{"id": i, "jti": f"test-{i}"} for i in range(1, 501)]
    auth_service.db.select.side_effect = [first, [{"id": 501, "jti": "test-501"}]]
    with auth_service.app.test_request_context():
        assert auth_service.revoked_tokens.__wrapped__().json["jtis"][-1] == "test-501"
    assert [call.kwargs["id__gt"] for call in auth_service.db.select.call_args_list] == [0, 500]


def test_registration_resumes_after_a_lost_profile_response(auth_service, monkeypatch):
    db = auth_service.db
    pending = {}

    def insert(table, values):
        pending.update(id=1, **values)
        return pending

    db.insert.side_effect = insert
    db.select.side_effect = lambda *args, **kwargs: [pending] if pending else []
    player_call = Mock(side_effect=[requests.Timeout(), SimpleNamespace(status_code=200)])
    monkeypatch.setattr(auth_service, "call_service", player_call)
    client = auth_service.app.test_client()
    form = {"username": "ash", "password": "example-password"}
    assert client.post("/auth/register", json=form).status_code == 503
    db.update.assert_not_called()
    assert client.post("/auth/register", json={**form, "password": "different-password"}).status_code == 409
    response = client.post("/auth/register", json=form)
    assert response.status_code == 201
    assert response.json["player_id"] == pending["player_id"]
    assert player_call.call_args_list[0] == player_call.call_args_list[1]
    db.insert.assert_called_once()
    db.delete.assert_not_called()
    db.update.assert_called_once_with("credentials", 1, {"registration_complete": True})
