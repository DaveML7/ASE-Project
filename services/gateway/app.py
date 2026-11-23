"""gateway: the only endpoint clients talk to.

Validates the JWT, refuses revoked tokens, rate-limits abusive clients, forwards ONLY the
documented routes over HTTPS and returns the upstream response unchanged. No business logic.

* deny by default: see `route_table`;
* the player id comes from the verified token, never from the client;
* tokens are verified with the PUBLIC key only (the gateway cannot mint them);
* the gateway holds no database or internal secret; its only service identity is the right
  to ask the auth service for the revocation list.
"""

import os

import jwt
from ase_common import (
    SERVICE_URLS,
    call_service,
    create_app,
    decode_token,
    error,
    guarded_request,
    peer_session,
)
from flask import Response, jsonify, request
from revocation import RevocationList, RevocationUnavailable
from route_table import is_public, match
from throttling import Throttle

app = create_app("gateway", public_health=True)

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:8000")
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "15"))
CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "2"))
REVOCATION_TIMEOUT = (1.0, 2.0)  # the refresh runs on a request thread: never wait long for it
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
FORWARDED_HEADERS = ("Authorization", "Content-Type", "Accept", "If-None-Match")


def _fetch_revoked() -> list[str]:
    response = call_service("auth", "GET", "/internal/revoked", timeout=REVOCATION_TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"revocation list refused: {response.status_code}")
    jtis = response.json()["jtis"]
    if not isinstance(jtis, list) or not all(isinstance(jti, str) for jti in jtis):
        raise ValueError("invalid revocation list")
    return jtis


revocations = RevocationList(_fetch_revoked, float(os.getenv("REVOKED_REFRESH_SECONDS", "5")))
throttle = Throttle()


def _authenticate() -> tuple[dict | None, tuple | None]:
    """Verify the bearer token and its revocation state: (claims, None) or (None, error)."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None, error(401, "missing bearer token")
    try:
        claims = decode_token(header.removeprefix("Bearer ").strip())
    except (jwt.PyJWTError, ValueError):
        return None, error(401, "invalid or expired token")
    try:
        if revocations.is_revoked(claims["jti"]):
            return None, error(401, "token has been revoked")
    except RevocationUnavailable:
        return None, error(503, "revocation state unavailable")
    return claims, None


def _too_many(wait: int):
    body, status = error(429, "too many requests, slow down")
    response = jsonify(body)
    response.status_code = status
    response.headers["Retry-After"] = str(wait)
    return response


def _answer(upstream, service: str, path: str) -> Response:
    """Rebuild the upstream answer, keeping only the headers the browser needs."""
    response = Response(
        upstream.content, upstream.status_code, content_type=upstream.headers.get("Content-Type")
    )
    # Services mark private data `no-store` and the public artwork cacheable: keep their choice.
    for header in ("Cache-Control", "ETag"):
        if header in upstream.headers:
            response.headers[header] = upstream.headers[header]
    if (service, path) == ("history", "") and "Link" in upstream.headers:
        response.headers["Link"] = upstream.headers["Link"]  # pagination: rel="next"
    return response


@app.route("/api/v1/<service>/", defaults={"path": ""}, methods=METHODS, strict_slashes=False)
@app.route("/api/v1/<service>/<path:path>", methods=METHODS, strict_slashes=False)
def proxy(service: str, path: str):
    if request.method == "OPTIONS":
        return "", 204

    found, allowed = match(service, request.method, path)
    if not found:
        return error(404, "unknown API route")
    if not allowed:
        return error(405, "method not allowed")

    claims = None
    if not is_public(service, request.method, path):
        claims, failure = _authenticate()
        if failure:
            return failure

    wait = throttle.retry_after(
        service,
        request.method,
        path,
        request.remote_addr or "unknown",
        claims["sub"] if claims else "",
        request.get_json(silent=True) if (service, path) == ("auth", "login") else None,
    )
    if wait:
        return _too_many(wait)

    # Only public routes of the services are reachable, and they verify the forwarded bearer
    # token themselves; the gateway adds no credential of its own.
    upstream = guarded_request(
        peer_session(),
        service,
        request.method,
        f"{SERVICE_URLS[service].rstrip('/')}/{service}/{path}".rstrip("/"),
        timeout=(CONNECT_TIMEOUT, UPSTREAM_TIMEOUT),
        params=request.args,
        data=request.get_data(),
        headers={n: request.headers[n] for n in FORWARDED_HEADERS if n in request.headers},
        allow_redirects=False,
    )
    if (service, request.method, path) == (
        "auth",
        "POST",
        "logout",
    ) and upstream.status_code == 204:
        revocations.revoke_locally(claims["jti"], claims["exp"])
    return _answer(upstream, service, path)


@app.after_request
def cors(response):
    """The frontend runs outside Docker, so it needs CORS on the gateway."""
    response.headers["Access-Control-Allow-Origin"] = FRONTEND_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, If-None-Match"
    exposed = [h for h in ("Link", "ETag") if h in response.headers]
    if exposed:
        response.headers["Access-Control-Expose-Headers"] = ", ".join(exposed)
    response.headers["Access-Control-Allow-Methods"] = ", ".join(METHODS)
    response.headers["Vary"] = "Origin"
    return response
