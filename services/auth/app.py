"""auth: registration, login and JWT issuing (US-01, US-02)."""

import re
import uuid

import requests
from flask import g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from ase_common import (
    JWT_TTL_SECONDS,
    DB,
    DbError,
    call_service,
    create_app,
    issue_token,
    require_jwt,
)

app = create_app("auth")
db = DB()

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
        return {
            "error": "Bad Request",
            "message": "username must be 3-20 characters: letters, digits or underscore",
        }, 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return {
            "error": "Bad Request",
            "message": f"password must be at least {MIN_PASSWORD_LENGTH} characters",
        }, 400
    if db.select("credentials", username=username):
        return {"error": "Conflict", "message": "username already taken"}, 409

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
        return {"error": "Conflict", "message": "username already taken"}, 409

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
        return {"error": "Service Unavailable", "message": "could not create player, retry"}, 503

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
    if not rows or not check_password_hash(rows[0]["password_hash"], password):
        return {"error": "Unauthorized", "message": "invalid username or password"}, 401
    return {
        "access_token": issue_token(str(rows[0]["player_id"]), username),
        "token_type": "Bearer",
        "expires_in": JWT_TTL_SECONDS,
    }


@app.get("/auth/me")
@require_jwt
def me():
    return jsonify(player_id=g.player_id, username=g.username)
