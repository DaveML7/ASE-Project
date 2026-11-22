"""Flask app factory shared by every microservice."""

import re
import uuid

import requests
from flask import Flask, g, jsonify, request
from requests import RequestException
from werkzeug.exceptions import HTTPException

from .clients import REQUEST_ID_HEADER, CircuitOpen, DbError
from .http import error
from .keys import jwt_private_key, jwt_public_key, service_private_key
from .secrets import read_secret

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


LOOPBACK = {"127.0.0.1", "::1"}
VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _rid() -> str:
    return getattr(g, "request_id", "-")


def create_app(
    name: str,
    required_secrets: tuple[str, ...] = (),
    required_keys: tuple[str, ...] = ("service", "jwt_public"),
    public_health: bool = False,
) -> Flask:
    """Build a service app. Fails fast if a required secret or key is missing.

    `/health` answers only to the container's own healthcheck (loopback) unless
    `public_health` is set (the gateway): other containers cannot probe services.
    """
    for secret in required_secrets:
        read_secret(secret)
    loaders = {
        "service": service_private_key,
        "jwt_public": jwt_public_key,
        "jwt_private": jwt_private_key,
    }
    for key in required_keys:
        loaders[key]()

    app = Flask(name)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.json.sort_keys = False

    @app.get("/health")
    def health():
        if not public_health and request.remote_addr not in LOOPBACK:
            return error(404, "not found")
        return {"status": "ok"}

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        return jsonify(error=exc.name, message=exc.description), exc.code

    @app.errorhandler(DbError)
    def db_error(exc: DbError):
        app.logger.error("[%s] db-manager call failed: %s", _rid(), exc)
        return error(502, "persistence layer unavailable")

    @app.errorhandler(CircuitOpen)
    def circuit_open(exc: CircuitOpen):
        app.logger.warning("[%s] %s", _rid(), exc)
        body, status = error(503, "a dependency is temporarily unavailable, retry shortly")
        response = jsonify(body)
        response.status_code = status
        response.headers["Retry-After"] = str(int(exc.retry_after) + 1)
        return response

    @app.errorhandler(requests.Timeout)
    def upstream_timeout(exc: requests.Timeout):
        app.logger.error("[%s] upstream call timed out: %s", _rid(), exc)
        return error(504, "upstream service timed out")

    @app.errorhandler(RequestException)
    def upstream_error(exc: RequestException):
        app.logger.error("[%s] upstream call failed: %s", _rid(), exc)
        return error(502, "upstream service unavailable")

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        app.logger.exception("[%s] unhandled error", _rid())
        return error(500, "unexpected error")

    @app.before_request
    def assign_request_id():
        """Keep a well-formed id from the caller (gateway) or create one: one id per user
        request across every service log line, echoed in the response."""
        supplied = request.headers.get(REQUEST_ID_HEADER, "")
        g.request_id = supplied if VALID_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex[:16]

    @app.after_request
    def security_headers(response):
        # Pure JSON API: nothing may be framed, sniffed, cached or loaded from it.
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        response.headers[REQUEST_ID_HEADER] = getattr(g, "request_id", "-")
        return response

    return app
