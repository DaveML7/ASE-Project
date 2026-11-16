"""auth: registration, login and JWT issuing (US-01, US-02)."""

import re
import uuid
from datetime import datetime, timezone

import requests
from ase_common import (
    DB,
    JWT_TTL_SECONDS,
    DbError,
    call_service,
    create_app,
    error,
    issue_token,
    require_jwt,
    require_service,
)
from flask import g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

app = create_app("auth", required_keys=("service", "jwt_public", "jwt_private"))
db = DB()

# Verified when the username does not exist, so a login costs the same whether or
# not the account exists (no timing oracle for user enumeration).
DUMMY_HASH = generate_password_hash(uuid.uuid4().hex)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MIN_PASSWORD_LENGTH = 8


def _credentials_body():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    return username, password


@app.post("/auth/register")
def register():
    username, password = _credentials_body()
    if not USERNAME_PATTERN.match(username):
        return error(400, "username must be 3-20 characters: letters, digits or underscore",)
    if len(password) < MIN_PASSWORD_LENGTH:
        return error(400, f"password must be at least {MIN_PASSWORD_LENGTH} characters",)
    if db.select("credentials", username=username):
        return error(409, "username already taken")

    player_id = str(uuid.uuid4())
    try:
        db.insert(
            "credentials",
            {
                "player_id": player_id,
                "username": username,
                "password_hash": generate_password_hash(password),
            },
        )
    except DbError:
        return error(409, "username already taken")

    # The players service creates the profile and the starter collection (US-06).
    try:
        response = call_service(
            "players",
            "POST",
            "/internal/players",
            json={"player_id": player_id, "username": username},
        )
        created = response.status_code < 400
    except requests.RequestException:
        created = False
    if not created:
        # Compensate: never leave a credential without a player profile.
        db.delete("credentials", db.select("credentials", player_id=player_id)[0]["id"])
        return error(503, "could not create player, retry")

    return {
        "player_id": player_id,
        "username": username,
        "access_token": issue_token(player_id, username),
        "token_type": "Bearer",
        "expires_in": JWT_TTL_SECONDS,
    }, 201


@app.post("/auth/login")
def login():
    username, password = _credentials_body()
    rows = db.select("credentials", username=username) if username else []
    stored = rows[0]["password_hash"] if rows else DUMMY_HASH
    password_ok = check_password_hash(stored, password)
    if not rows or not password_ok:
        return error(401, "invalid username or password")
    return {
        "access_token": issue_token(str(rows[0]["player_id"]), username),
        "token_type": "Bearer",
        "expires_in": JWT_TTL_SECONDS,
    }


@app.get("/auth/me")
@require_jwt
def me():
    return jsonify(player_id=g.player_id, username=g.username)


@app.post("/auth/logout")
@require_jwt
def logout():
    """Revoke the presented token: it stops working at the gateway immediately."""
    expires = datetime.fromtimestamp(g.token_exp, tz=timezone.utc).isoformat()
    try:
        db.insert("revoked_tokens", {"jti": g.token_id, "player_id": g.player_id, "expires_at": expires})
    except DbError as exc:
        if not exc.is_conflict:  # already revoked is fine
            raise
    return "", 204


@app.get("/internal/revoked")
@require_service("gateway")
def revoked_tokens():
    """Ids of revoked tokens that have not expired yet (expired ones are harmless)."""
    now = datetime.now(timezone.utc).isoformat()
    rows = db.select("revoked_tokens", order_by="id", limit=500, expires_at__gt=now)
    return jsonify(jtis=[row["jti"] for row in rows])
