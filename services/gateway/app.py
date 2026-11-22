"""gateway: the only endpoint clients talk to.

Validates the JWT, rate-limits abusive clients, forwards ONLY the documented routes
over HTTPS and returns the upstream response unchanged. No business logic lives here.

Security properties:

* deny by default: a request is forwarded only if (service, method, path) matches the
  route table below, so path tricks (``..``, encoded dots, extra segments) can never
  reach another endpoint, in particular the ``/internal/*`` routes;
* the player id comes from the verified token, never from the client;
* tokens are verified with the PUBLIC key only (the gateway cannot mint them) and a
  token revoked by logout is refused at once (revocation list, refreshed every few
  seconds from the auth service and updated immediately on logout).

The gateway holds no database or internal secret; its only service identity is the
right to ask the auth service for the revocation list.
"""

import logging
import os
import re
import time
from threading import Lock

import jwt
import requests
from ase_common import (
    SERVICE_URLS,
    RateLimiter,
    call_service,
    create_app,
    decode_token,
    error,
    guarded_request,
    peer_session,
)
from flask import Response, jsonify, request

app = create_app("gateway", public_health=True)
limiter = RateLimiter()
log = logging.getLogger("gateway")

REVOKED_REFRESH_SECONDS = float(os.getenv("REVOKED_REFRESH_SECONDS", "5"))
REVOCATION_TIMEOUT = (1.0, 2.0)  # the refresh runs on a request thread: never wait long for it
_revoked: set[str] | None = None
_local_revocations: dict[str, int] = {}
_revoked_synced = 0.0
_refresh_lock = Lock()
_local_lock = Lock()


def _refresh_revoked() -> None:
    """Reload the revocation list at most every few seconds. If the auth service is
    unreachable the previous list is kept (nobody can log in or out meanwhile)."""
    global _revoked, _revoked_synced
    if not _refresh_lock.acquire(blocking=False):
        return
    try:
        now = time.monotonic()
        if now - _revoked_synced < REVOKED_REFRESH_SECONDS:
            return
        _revoked_synced = now
        with _local_lock:
            expired = [jti for jti, expiry in _local_revocations.items() if expiry <= time.time()]
            for jti in expired:
                del _local_revocations[jti]
        response = call_service("auth", "GET", "/internal/revoked", timeout=REVOCATION_TIMEOUT)
        if response.status_code != 200:
            log.warning("revocation list refused: %s", response.status_code)
            return
        jtis = response.json()["jtis"]
        if not isinstance(jtis, list) or not all(isinstance(jti, str) for jti in jtis):
            raise ValueError("invalid revocation list")
        _revoked = set(jtis)
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        log.warning("revocation list unavailable: %s", exc)
    finally:
        _refresh_lock.release()


FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:8000")
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "15"))
CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "2"))
ID = r"[0-9]{1,18}"

# (service, allowed methods, regex that the WHOLE remaining path must match)
ROUTE_TABLE = [
    ("auth", {"POST"}, r"register|login"),
    ("auth", {"GET"}, r"me"),
    ("auth", {"POST"}, r"logout"),
    ("players", {"GET", "PATCH"}, r"me"),
    ("players", {"GET"}, r"me/monsters"),
    ("monsters", {"GET"}, rf"|{ID}|{ID}/image"),
    ("teams", {"GET", "POST"}, r""),
    ("teams", {"GET", "PUT", "DELETE"}, ID),
    ("teams", {"POST"}, rf"{ID}/validate"),
    ("encounters", {"GET", "POST"}, r""),
    ("encounters", {"GET"}, ID),
    ("history", {"GET"}, rf"|{ID}"),
]
ROUTES = [(service, methods, re.compile(pattern)) for service, methods, pattern in ROUTE_TABLE]
PUBLIC = {("auth", "register"), ("auth", "login")}  # everything else needs a token...
PUBLIC_GET_SERVICES = {"monsters"}  # ...except the read-only catalogue

# Abuse limits per minute (env-tunable). Login is limited per client and per
# client+username so a distributed guess against one account is still throttled.
LIMITS = {
    "login_ip": int(os.getenv("RATE_LIMIT_LOGIN_PER_IP", "60")),
    "login_user": int(os.getenv("RATE_LIMIT_LOGIN_PER_USER", "10")),
    "register": int(os.getenv("RATE_LIMIT_REGISTER_PER_IP", "30")),
    "encounter": int(os.getenv("RATE_LIMIT_ENCOUNTERS_PER_PLAYER", "30")),
}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
FORWARDED_HEADERS = ("Authorization", "Content-Type", "Accept")


def _match(service: str, method: str, path: str):
    """Return (found, allowed): the path exists, and the method is allowed on it."""
    found = False
    for name, methods, pattern in ROUTES:
        if name == service and pattern.fullmatch(path):
            found = True
            if method in methods:
                return True, True
    return found, False


def _is_public(service: str, method: str, path: str) -> bool:
    return (service, path) in PUBLIC or (service in PUBLIC_GET_SERVICES and method == "GET")


def _throttle(service: str, method: str, path: str, player_id: str):
    """Return a 429 response if the caller exceeded one of the abuse limits."""
    client = request.remote_addr or "unknown"
    checks = []
    if (service, method, path) == ("auth", "POST", "login"):
        body = request.get_json(silent=True)  # a malformed body is the auth service's 400
        username = str(body.get("username", ""))[:64].lower() if isinstance(body, dict) else ""
        checks = [
            (f"login:ip:{client}", LIMITS["login_ip"]),
            (f"login:user:{client}:{username}", LIMITS["login_user"]),
        ]
    elif (service, method, path) == ("auth", "POST", "register"):
        checks = [(f"register:{client}", LIMITS["register"])]
    elif (service, method, path) == ("encounters", "POST", ""):
        checks = [(f"encounter:{player_id}", LIMITS["encounter"])]
    for key, limit in checks:
        wait = limiter.check(key, limit)
        if wait:
            body, status = error(429, "too many requests, slow down")
            response = jsonify(body)
            response.status_code = status
            response.headers["Retry-After"] = str(int(wait) + 1)
            return response
    return None


@app.route("/api/v1/<service>/", defaults={"path": ""}, methods=METHODS, strict_slashes=False)
@app.route("/api/v1/<service>/<path:path>", methods=METHODS, strict_slashes=False)
def proxy(service: str, path: str):
    if request.method == "OPTIONS":
        return "", 204

    found, allowed = _match(service, request.method, path)
    if not found:
        return error(404, "unknown API route")
    if not allowed:
        return error(405, "method not allowed")

    player_id, token_id = "", ""
    if not _is_public(service, request.method, path):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return error(401, "missing bearer token")
        try:
            claims = decode_token(header.removeprefix("Bearer ").strip())
        except (jwt.PyJWTError, ValueError):
            return error(401, "invalid or expired token")
        player_id, token_id = claims["sub"], claims["jti"]
        _refresh_revoked()
        revoked = _revoked
        if revoked is None:
            return error(503, "revocation state unavailable")
        with _local_lock:
            if token_id in revoked or token_id in _local_revocations:
                return error(401, "token has been revoked")

    throttled = _throttle(service, request.method, path, player_id)
    if throttled is not None:
        return throttled

    # Only the services' public routes are reachable, and they verify the forwarded
    # bearer token themselves; the gateway adds no credential of its own.
    headers = {name: request.headers[name] for name in FORWARDED_HEADERS if name in request.headers}
    upstream = guarded_request(
        peer_session(),
        service,
        request.method,
        f"{SERVICE_URLS[service].rstrip('/')}/{service}/{path}".rstrip("/"),
        timeout=(CONNECT_TIMEOUT, UPSTREAM_TIMEOUT),
        params=request.args,
        data=request.get_data(),
        headers=headers,
        allow_redirects=False,
    )
    if (service, request.method, path) == (
        "auth",
        "POST",
        "logout",
    ) and upstream.status_code == 204:
        with _local_lock:
            _local_revocations[token_id] = claims["exp"]
    response = Response(
        upstream.content,
        upstream.status_code,
        content_type=upstream.headers.get("Content-Type"),
    )
    # Services mark private data `no-store` and the public artwork cacheable: keep their choice.
    if "Cache-Control" in upstream.headers:
        response.headers["Cache-Control"] = upstream.headers["Cache-Control"]
    return response


@app.after_request
def cors(response):
    """The frontend runs outside Docker, so it needs CORS on the gateway."""
    response.headers["Access-Control-Allow-Origin"] = FRONTEND_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = ", ".join(METHODS)
    response.headers["Vary"] = "Origin"
    return response
