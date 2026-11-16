"""Service-to-service identity: Ed25519 service tokens verified with the caller's public key."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from ase_common import clear_cache, require_jwt, require_service, service_token
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from conftest import forge_hs256
from flask import Flask, g, jsonify


@pytest.fixture
def callee(key_dir, monkeypatch):
    """A 'players' service that accepts 'encounters' and 'teams' only."""
    app = Flask("players")

    @app.get("/internal/x")
    @require_service("encounters", "teams")
    def internal():
        return jsonify(caller=g.caller)

    @app.get("/internal/owned/<player_id>")
    @require_service("encounters")
    @require_jwt
    def owned(player_id):
        if g.player_id != player_id:
            return {"error": "Forbidden"}, 403
        return jsonify(ok=True)

    def sign_as(name, audience="players", key=None):
        monkeypatch.setenv("SERVICE_NAME", name)
        signing_key = key or key_dir.identity(name)
        token = service_token(audience)
        if key is None:
            key_dir.grant(name, signing_key)  # init job would hand out this public key
        monkeypatch.setenv("SERVICE_NAME", "players")  # now act as the callee
        clear_cache()
        return token

    return app.test_client(), sign_as, key_dir


def call(client, token=None, path="/internal/x", **headers):
    if token:
        headers["X-Service-Token"] = token
    return client.get(path, headers=headers)


def test_allowed_caller_is_identified(callee):
    client, sign_as, _ = callee
    response = call(client, sign_as("encounters"))
    assert response.status_code == 200 and response.json == {"caller": "encounters"}


def test_missing_or_garbage_token_is_refused(callee):
    client, _, _ = callee
    assert call(client).status_code == 403
    assert call(client, "not.a.jwt").status_code == 403


def test_caller_not_in_the_allowed_list_is_refused_even_with_a_valid_signature(callee):
    client, sign_as, _ = callee
    assert call(client, sign_as("monsters")).status_code == 403


def test_caller_without_a_distributed_public_key_is_refused(callee, monkeypatch):
    client, _, key_dir = callee
    monkeypatch.setenv("SERVICE_NAME", "teams")
    key_dir.identity("teams")  # signs, but init never granted teams.pub to this callee
    token = service_token("players")
    monkeypatch.setenv("SERVICE_NAME", "players")
    assert call(client, token).status_code == 403


def test_token_for_another_audience_is_refused(callee):
    client, sign_as, _ = callee
    assert call(client, sign_as("encounters", audience="teams")).status_code == 403


def test_forged_signature_is_refused(callee):
    client, sign_as, key_dir = callee
    sign_as("encounters")  # public key of the real encounters is now granted
    forger = ed25519.Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    forged = jwt.encode(
        {"iss": "encounters", "aud": "players", "iat": now, "exp": now + timedelta(seconds=60)},
        forger, algorithm="EdDSA",
    )
    assert call(client, forged).status_code == 403


def test_expired_service_token_is_refused(callee):
    client, sign_as, key_dir = callee
    sign_as("encounters")  # grants encounters.pub; service.key still holds the matching private key
    signing = serialization.load_pem_private_key((key_dir.path / "service.key").read_bytes(), password=None)
    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    expired = jwt.encode(
        {"iss": "encounters", "aud": "players", "iat": old, "exp": old + timedelta(seconds=60)},
        signing, algorithm="EdDSA",
    )
    assert call(client, expired).status_code == 403


def test_hs256_and_unsigned_tokens_are_refused(callee):
    client, sign_as, key_dir = callee
    sign_as("encounters")
    public = (key_dir.path / "encounters.pub").read_bytes()
    now = datetime.now(timezone.utc)
    claims = {"iss": "encounters", "aud": "players", "exp": now + timedelta(seconds=60)}
    # classic algorithm-confusion: sign with HS256 using the public key as the HMAC secret
    confused = forge_hs256({**claims, "exp": int(claims["exp"].timestamp())}, public)
    assert call(client, confused).status_code == 403
    assert call(client, jwt.encode(claims, None, algorithm="none")).status_code == 403


def test_internal_route_also_needs_the_owners_user_token(callee):
    from ase_common import issue_token

    client, sign_as, _ = callee
    service = sign_as("encounters")
    alice, mallory = issue_token("alice", "a"), issue_token("mallory", "m")
    headers = {"X-Service-Token": service}
    assert client.get("/internal/owned/alice", headers=headers).status_code == 401  # no user token
    assert client.get("/internal/owned/alice", headers={**headers, "Authorization": f"Bearer {mallory}"}).status_code == 403
    assert client.get("/internal/owned/alice", headers={**headers, "Authorization": f"Bearer {alice}"}).status_code == 200
