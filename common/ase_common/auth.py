"""JWT issuing/verification and the shared internal service key."""

import hmac
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import g, jsonify, request

from .secrets import internal_api_key, jwt_secret

JWT_ALGORITHM = "HS256"
JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", "3600"))


def issue_token(player_id: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": player_id,
        "username": username,
        "iat": now,
        "exp": now + timedelta(seconds=JWT_TTL_SECONDS),
    }
    return jwt.encode(payload, jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        jwt_secret(),
        algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "sub"]},
    )


def _unauthorized(message: str):
    return jsonify(error="Unauthorized", message=message), 401


def require_jwt(view):
    """Reject requests without a valid bearer token; expose g.player_id."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return _unauthorized("missing bearer token")
        try:
            payload = decode_token(header.removeprefix("Bearer ").strip())
        except jwt.PyJWTError:
            return _unauthorized("invalid or expired token")
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
            return jsonify(error="Forbidden", message="invalid internal key"), 403
        return view(*args, **kwargs)

    return wrapper
