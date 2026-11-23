"""auth: registration, login and JWT issuing (US-01, US-02)."""

import re
import uuid
from datetime import datetime, timezone

from ase_common import (
    DB,
    JWT_TTL_SECONDS,
    DbError,
    call_service,
    create_app,
    error,
    issue_token,
    json_object,
    require_jwt,
    require_service,
)
from flask import abort, g, jsonify
from registration import register_account
from werkzeug.security import check_password_hash, generate_password_hash

app = create_app("auth", required_keys=("service", "jwt_public", "jwt_private"))
db = DB()

# Verified when the username does not exist, so a login costs the same whether or
# not the account exists (no timing oracle for user enumeration).
DUMMY_HASH = generate_password_hash(uuid.uuid4().hex)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128  # scrypt cost must not depend on attacker-sized input


def _credentials_body():
    body = json_object()
    username, password = body.get("username", ""), body.get("password", "")
    if not isinstance(username, str) or not isinstance(password, str):
        abort(400, "username and password must be strings")
    return username.strip(), password


@app.post("/auth/register")
def register():
    username, password = _credentials_body()
    if not USERNAME_PATTERN.match(username):
        return error(
            400,
            "username must be 3-20 characters: letters, digits or underscore",
        )
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        return error(
            400,
            f"password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters",
        )
    return register_account(db, call_service, username, password)


@app.post("/auth/login")
def login():
    username, password = _credentials_body()
    # A name that cannot exist (wrong shape, oversized, NUL) never reaches the database; it
    # still pays for one hash check below, so the answer and the cost stay the same.
    rows = (
        db.select("credentials", username=username, registration_complete=True, limit=1)
        if USERNAME_PATTERN.match(username)
        else []
    )
    stored = rows[0]["password_hash"] if rows else DUMMY_HASH
    # An oversized password is checked against the dummy hash: same cost, always refused.
    password_ok = check_password_hash(stored, password[: MAX_PASSWORD_LENGTH + 1])
    password_ok = password_ok and len(password) <= MAX_PASSWORD_LENGTH
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
        db.insert(
            "revoked_tokens", {"jti": g.token_id, "player_id": g.player_id, "expires_at": expires}
        )
    except DbError as exc:
        if not exc.conflicts_with("revoked_tokens_jti_key"):
            raise
    return "", 204


@app.get("/internal/revoked")
@require_service("gateway")
def revoked_tokens():
    """Ids of revoked tokens that have not expired yet (expired ones are harmless)."""
    now = datetime.now(timezone.utc).isoformat()
    jtis, last_id = [], 0
    while True:
        rows = db.select(
            "revoked_tokens", order_by="id", limit=500, expires_at__gt=now, id__gt=last_id
        )
        jtis.extend(row["jti"] for row in rows)
        if len(rows) < 500:
            return jsonify(jtis=jtis)
        last_id = rows[-1]["id"]
