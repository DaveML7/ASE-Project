"""User tokens (EdDSA JWT) and service-to-service identity (Ed25519 service tokens).

User tokens are issued by the auth service with its private key and verified by
everyone with the public key. Service tokens are short-lived JWTs signed by the
calling service; the callee only accepts callers whose public key it was given.
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import g, request

from .http import error
from .keys import KeyError_, jwt_private_key, jwt_public_key, peer_public_key, service_private_key

JWT_ALGORITHM = "EdDSA"
JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", "1800"))
JWT_ISSUER = "monster-tamers-auth"
JWT_AUDIENCE = "monster-tamers-api"
SERVICE_TOKEN_TTL = 60
SERVICE_TOKEN_REUSE = 40  # reuse a token for this long (< TTL: the callee always sees >= 20 s left)
_service_tokens: dict[tuple, tuple[float, str]] = {}
SERVICE_HEADER = "X-Service-Token"
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


def service_token(audience: str) -> str:
    """Short-lived token proving which service is calling `audience`.

    A token is valid for 60 s and is reused for 40 s per audience, so a request that
    makes several internal calls signs once. The cache key includes the identity of the
    private key object, so a key reload never serves a token signed with an old key.
    """
    issuer, key = os.environ["SERVICE_NAME"], service_private_key()
    cache_key = (issuer, audience, id(key))
    cached = _service_tokens.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    now = datetime.now(timezone.utc)
    payload = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + timedelta(seconds=SERVICE_TOKEN_TTL),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, key, algorithm=JWT_ALGORITHM)
    _service_tokens[cache_key] = (time.monotonic() + SERVICE_TOKEN_REUSE, token)
    return token


def require_service(*allowed_callers: str):
    """Accept only the listed services, proven by a signature checked against the
    caller's PUBLIC key. The init job only hands out the public keys of allowed
    callers, so an unlisted service fails even if the code were changed."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            token = request.headers.get(SERVICE_HEADER, "")
            try:
                caller = jwt.decode(token, options={"verify_signature": False}).get("iss")
                if caller not in allowed_callers:
                    raise ValueError("caller not allowed")
                jwt.decode(
                    token,
                    peer_public_key(caller),
                    algorithms=[JWT_ALGORITHM],
                    audience=os.environ["SERVICE_NAME"],
                    issuer=caller,
                    options={"require": ["exp", "iss", "aud"]},
                )
            except VERIFY_ERRORS:
                return error(403, "invalid service identity")
            g.caller = caller
            return view(*args, **kwargs)

        return wrapper

    return decorator
