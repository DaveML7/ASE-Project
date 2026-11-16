"""JWT issuing/verification and the shared internal service key."""

import hmac
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import g, request

from .http import error
from .secrets import internal_api_key, jwt_secret

JWT_ALGORITHM = "HS256"
JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", "3600"))
JWT_ISSUER = "monster-tamers-auth"
JWT_AUDIENCE = "monster-tamers-api"


def issue_token(player_id: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": player_id,
        "username": username,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=JWT_TTL_SECONDS),
    }
    return jwt.encode(payload, jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        jwt_secret(),
        algorithms=[JWT_ALGORITHM],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
        options={"require": ["exp", "sub", "iss", "aud"]},
    )


def require_jwt(view):
    """Reject requests without a valid bearer token; expose g.player_id."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return error(401, "missing bearer token")
        try:
            payload = decode_token(header.removeprefix("Bearer ").strip())
        except jwt.PyJWTError:
            return error(401, "invalid or expired token")
        g.player_id = payload["sub"]
        g.username = payload.get("username")
        return view(*args, **kwargs)

    return wrapper


def require_internal(view):
    """Service-to-service calls must carry the shared internal key."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-Internal-Key", "")
        if not hmac.compare_digest(key.encode(), internal_api_key().encode()):
            return error(403, "invalid internal key")
        return view(*args, **kwargs)

    return wrapper
