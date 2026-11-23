"""Service-to-service identity: short-lived Ed25519 tokens verified with the caller's public key.

A service signs a 60-second token for the service it calls; the callee only accepts callers
whose public key the init job handed to it.
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import g, request

from .http import error
from .keys import peer_public_key, service_private_key
from .tokens import JWT_ALGORITHM, VERIFY_ERRORS

SERVICE_TOKEN_TTL = 60
SERVICE_TOKEN_REUSE = 40  # reuse a token for this long (< TTL: the callee always sees >= 20 s left)
SERVICE_HEADER = "X-Service-Token"
_service_tokens: dict[tuple, tuple[float, str]] = {}


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
