"""Flask app factory shared by every microservice."""

from flask import Flask, jsonify
from requests import RequestException
from werkzeug.exceptions import HTTPException

from .clients import DbError


def create_app(name: str) -> Flask:
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
        return jsonify(error="Bad Gateway", message="persistence layer unavailable"), 502

    @app.errorhandler(RequestException)
    def upstream_error(exc: RequestException):
        app.logger.error("upstream call failed: %s", exc)
        return jsonify(error="Bad Gateway", message="upstream service unavailable"), 502

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        app.logger.exception("unhandled error")
        return jsonify(error="Internal Server Error", message="unexpected error"), 500

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    return app
