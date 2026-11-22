"""One shape for every error response, plus the request-parsing helpers every service needs."""

from flask import abort, request

PHRASES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


def error(status: int, message: str, **extra):
    """Return a (body, status) pair ready to be returned from a Flask view."""
    return {"error": PHRASES[status], "message": message, **extra}, status


def json_object() -> dict:
    """The request body as a dict. A missing body is {}; anything that is not a JSON
    object (array, string, number) is a client error, never a server crash."""
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        abort(400, "the request body must be a JSON object")
    return body


def parse_limit(default: int, maximum: int, name: str = "limit") -> int:
    """Read the `limit` query parameter: an integer from 1 to `maximum` (larger values are
    capped). Anything else is a 400, the same answer in every service."""
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        abort(400, f"{name} must be an integer")
    if value < 1:
        abort(400, f"{name} must be at least 1")
    return min(value, maximum)
