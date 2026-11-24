"""Error handlers: every failure leaves a service as the same JSON shape."""

import requests
from flask import jsonify
from werkzeug.exceptions import HTTPException

from .db_client import DbError
from .http import error
from .request_context import current_request_id
from .resilience import CircuitOpen

CLIENT_OUTCOMES = {404: "not found", 409: "the request conflicts with the current state"}


def register_error_handlers(app) -> None:
    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        return jsonify(error=exc.name, message=exc.description), exc.code

    @app.errorhandler(DbError)
    def db_error(exc: DbError):
        # A row that vanished or a conflict found by the database is the request's outcome,
        # not an outage: report it as such (a 502 would also count against the circuit).
        if exc.status_code in CLIENT_OUTCOMES:
            app.logger.info("[%s] db-manager answered %s", current_request_id(), exc)
            return error(exc.status_code, CLIENT_OUTCOMES[exc.status_code])
        app.logger.error("[%s] db-manager call failed: %s", current_request_id(), exc)
        return error(502, "persistence layer unavailable")

    @app.errorhandler(CircuitOpen)
    def circuit_open(exc: CircuitOpen):
        app.logger.warning("[%s] %s", current_request_id(), exc)
        body, status = error(503, "a dependency is temporarily unavailable, retry shortly")
        response = jsonify(body)
        response.status_code = status
        response.headers["Retry-After"] = str(int(exc.retry_after) + 1)
        return response

    @app.errorhandler(requests.Timeout)
    def upstream_timeout(exc: requests.Timeout):
        app.logger.error("[%s] upstream call timed out: %s", current_request_id(), exc)
        return error(504, "upstream service timed out")

    @app.errorhandler(requests.RequestException)
    def upstream_error(exc: requests.RequestException):
        app.logger.error("[%s] upstream call failed: %s", current_request_id(), exc)
        return error(502, "upstream service unavailable")

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        app.logger.exception("[%s] unhandled error", current_request_id())
        return error(500, "unexpected error")
