"""The registration flow: create the credential, then the player, and finish only when both exist.

The credential is written first as *pending*; a timeout after the players service committed
keeps the pending credential, so a retry with the same password reuses the same player id and
never leaves a half-created account behind.
"""

import uuid

import requests
from ase_common import JWT_TTL_SECONDS, DbError, error, issue_token
from werkzeug.security import check_password_hash, generate_password_hash


def register_account(db, call_service, username: str, password: str):
    rows = db.select("credentials", username=username, limit=1)
    if rows:
        credential = rows[0]
        if credential["registration_complete"] or not check_password_hash(
            credential["password_hash"], password
        ):
            return error(409, "username already taken")
    else:
        try:
            credential = db.insert(
                "credentials",
                {
                    "player_id": str(uuid.uuid4()),
                    "username": username,
                    "password_hash": generate_password_hash(password),
                    "registration_complete": False,
                },
            )
        except DbError as exc:
            if exc.conflicts_with("credentials_username_key"):
                return error(409, "username already taken")
            raise

    # A timeout may follow a commit. Keep the pending credential so a retry uses the same ID.
    player_id = str(credential["player_id"])
    try:
        response = call_service(
            "players",
            "POST",
            "/internal/players",
            json={"player_id": player_id, "username": username},
        )
        created = response.status_code in {200, 201}
    except requests.RequestException:
        created = False
    if not created:
        return error(503, "could not create player, retry registration with the same credentials")
    db.update("credentials", credential["id"], {"registration_complete": True})

    return {
        "player_id": player_id,
        "username": username,
        "access_token": issue_token(player_id, username),
        "token_type": "Bearer",
        "expires_in": JWT_TTL_SECONDS,
    }, 201
