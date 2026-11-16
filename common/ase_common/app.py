"""Flask app factory shared by every microservice."""

from flask import Flask, jsonify
from requests import RequestException
from werkzeug.exceptions import HTTPException

from .clients import DbError
from .http import error
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


def create_app(name: str, required_secrets: tuple[str, ...] = ("INTERNAL_API_KEY", "JWT_SECRET")) -> Flask:
    # Fail fast: a service without its secrets must not start.
    for secret in required_secrets:
        read_secret(secret)

    app = Flask(name)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.json.sort_keys = False

    @app.get("/health")
    def health():
        return {"status": "ok", "service": name}

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        return jsonify(error=exc.name, message=exc.description), exc.code

    @app.errorhandler(DbError)
    def db_error(exc: DbError):
        app.logger.error("db-manager call failed: %s", exc)
        return error(502, "persistence layer unavailable")

    @app.errorhandler(RequestException)
    def upstream_error(exc: RequestException):
        app.logger.error("upstream call failed: %s", exc)
        return error(502, "upstream service unavailable")

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        app.logger.exception("unhandled error")
        return error(500, "unexpected error")

    @app.after_request
    def security_headers(response):
        # Pure JSON API: nothing may be framed, sniffed, cached or loaded from it.
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    return app
