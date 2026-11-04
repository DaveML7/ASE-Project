"""gateway: the only endpoint clients talk to.

Validates the JWT, forwards plain requests over HTTPS to the owning service and
returns its response unchanged. No business logic lives here.
"""

import os

import jwt
import requests
from flask import Response, jsonify, request

from ase_common import (
    CA_CERT,
    INTERNAL_API_KEY,
    SERVICE_URLS,
    create_app,
    decode_token,
)

app = create_app("gateway")

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:8000")
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "15"))

ROUTES = {name: url for name, url in SERVICE_URLS.items() if name != "db-manager"}
PUBLIC_ROUTES = {("auth", "register"), ("auth", "login")}
FORWARDED_HEADERS = ("Authorization", "Content-Type", "Accept")
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def _is_public(service: str, path: str) -> bool:
    return (service, path) in PUBLIC_ROUTES or (service == "monsters" and request.method == "GET")


def _unauthorized(message: str):
    return jsonify(error="Unauthorized", message=message), 401


@app.route("/api/v1/<service>/", defaults={"path": ""}, methods=METHODS, strict_slashes=False)
@app.route("/api/v1/<service>/<path:path>", methods=METHODS, strict_slashes=False)
def proxy(service: str, path: str):
    if request.method == "OPTIONS":
        return "", 204

    upstream = ROUTES.get(service)
    if upstream is None:
        return jsonify(error="Not Found", message=f"unknown API '{service}'"), 404

    player_id = ""
    if not _is_public(service, path):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return _unauthorized("missing bearer token")
        try:
            player_id = decode_token(header.removeprefix("Bearer ").strip())["sub"]
        except jwt.PyJWTError:
            return _unauthorized("invalid or expired token")

    headers = {name: request.headers[name] for name in FORWARDED_HEADERS if name in request.headers}
    headers["X-Internal-Key"] = INTERNAL_API_KEY
    if player_id:
        headers["X-Player-Id"] = player_id

    upstream_response = requests.request(
        method=request.method,
        url=f"{upstream.rstrip('/')}/{service}/{path}".rstrip("/"),
        params=request.args,
        data=request.get_data(),
        headers=headers,
        verify=CA_CERT,
        timeout=UPSTREAM_TIMEOUT,
        allow_redirects=False,
    )
    return Response(
        upstream_response.content,
        upstream_response.status_code,
        content_type=upstream_response.headers.get("Content-Type"),
    )


@app.after_request
def cors(response):
    """The frontend runs outside Docker, so it needs CORS on the gateway."""
    response.headers["Access-Control-Allow-Origin"] = FRONTEND_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = ", ".join(METHODS)
    return response
