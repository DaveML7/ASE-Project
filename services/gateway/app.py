"""gateway: the only endpoint clients talk to.

Validates the JWT, rate-limits abusive clients, forwards ONLY the documented routes
over HTTPS and returns the upstream response unchanged. No business logic lives here.

Security properties:

* deny by default: a request is forwarded only if (service, method, path) matches the
  route table below, so path tricks (``..``, encoded dots, extra segments) can never
  reach another endpoint, in particular the ``/internal/*`` routes;
* the internal service key is NEVER forwarded: public requests cannot use it;
* the player id comes from the verified token, never from the client.
"""

import os
import re

import jwt
from flask import Response, jsonify, request

from ase_common import (
    SERVICE_URLS,
    RateLimiter,
    create_app,
    decode_token,
    error,
    peer_session,
)

app = create_app("gateway", required_secrets=("JWT_SECRET",))
limiter = RateLimiter()

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:8000")
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "15"))
ID = r"[0-9]{1,18}"

# (service, allowed methods, regex that the WHOLE remaining path must match)
ROUTE_TABLE = [
    ("auth", {"POST"}, r"register|login"),
    ("auth", {"GET"}, r"me"),
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
        username = str((request.get_json(silent=True) or {}).get("username", ""))[:64].lower()
        checks = [(f"login:ip:{client}", LIMITS["login_ip"]),
                  (f"login:user:{client}:{username}", LIMITS["login_user"])]
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

    player_id = ""
    if not _is_public(service, request.method, path):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return error(401, "missing bearer token")
        try:
            player_id = decode_token(header.removeprefix("Bearer ").strip())["sub"]
        except jwt.PyJWTError:
            return error(401, "invalid or expired token")

    throttled = _throttle(service, request.method, path, player_id)
    if throttled is not None:
        return throttled

    # Only the services' public routes are reachable, and they authenticate with the
    # forwarded bearer token: the internal key is deliberately not attached.
    headers = {name: request.headers[name] for name in FORWARDED_HEADERS if name in request.headers}
    upstream = peer_session().request(
        method=request.method,
        url=f"{SERVICE_URLS[service].rstrip('/')}/{service}/{path}".rstrip("/"),
        params=request.args,
        data=request.get_data(),
        headers=headers,
        timeout=UPSTREAM_TIMEOUT,
        allow_redirects=False,
    )
    return Response(
        upstream.content,
        upstream.status_code,
        content_type=upstream.headers.get("Content-Type"),
    )


@app.after_request
def cors(response):
    """The frontend runs outside Docker, so it needs CORS on the gateway."""
    response.headers["Access-Control-Allow-Origin"] = FRONTEND_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = ", ".join(METHODS)
    response.headers["Vary"] = "Origin"
    return response
