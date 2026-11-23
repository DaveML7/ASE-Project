"""User tokens: EdDSA JWTs issued by the auth service and verified with the public key."""

import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import g, request

from .config import JWT_TTL_SECONDS
from .http import error
from .keys import KeyError_, jwt_private_key, jwt_public_key

JWT_ALGORITHM = "EdDSA"
JWT_ISSUER = "monster-tamers-auth"
JWT_AUDIENCE = "monster-tamers-api"
VERIFY_ERRORS = (jwt.PyJWTError, KeyError_, ValueError, TypeError)


def issue_token(player_id: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": player_id,
        "username": username,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "jti": uuid.uuid4().hex,  # lets a token be revoked individually (logout)
        "iat": now,
        "exp": now + timedelta(seconds=JWT_TTL_SECONDS),
    }
    return jwt.encode(payload, jwt_private_key(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        jwt_public_key(),
        algorithms=[JWT_ALGORITHM],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
        options={"require": ["exp", "sub", "iss", "aud", "jti"]},
    )


def bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    return header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""


def require_jwt(view):
    """Reject requests without a valid bearer token; expose g.player_id."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not bearer_token():
            return error(401, "missing bearer token")
        try:
            payload = decode_token(bearer_token())
        except VERIFY_ERRORS:
            return error(401, "invalid or expired token")
        g.player_id = payload["sub"]
        g.username = payload.get("username")
        g.token_id = payload["jti"]
        g.token_exp = payload["exp"]
        return view(*args, **kwargs)

    return wrapper
