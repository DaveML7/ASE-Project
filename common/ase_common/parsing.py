"""Reading and validating what a client sent: the same rules and the same answers everywhere."""

import re

from flask import abort, request

# a JSON \u0000 escape that is not itself escaped (preceded by an even run of backslashes)
_JSON_NUL = re.compile(rb"(?<!\\)(?:\\\\)*\\u0000", re.IGNORECASE)


def reject_nul_bytes() -> None:
    """PostgreSQL text cannot hold a NUL character: refuse it at the edge of every service
    (raw byte, JSON \\u0000 escape or %00 in the query) so it is a 400, never a 500."""
    body = request.get_data()
    query = request.query_string
    if b"\x00" in query or b"%00" in query.lower() or b"\x00" in body or _JSON_NUL.search(body):
        abort(400, "NUL characters are not allowed")


def json_object() -> dict:
    """The request body as a dict. A missing body is {}; anything that is not a JSON
    object (array, string, number) is a client error, never a server crash."""
    raw = request.get_data()
    if not raw.strip():
        return {}
    body = request.get_json(silent=True, force=True)  # whatever the Content-Type says
    if body is None and raw.strip() != b"null":
        abort(400, "the request body is not valid JSON")
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


def require_string(value, field: str, *, min_length: int = 0, max_length: int | None = None) -> str:
    """A string field of bounded length, or a 400 naming the field."""
    if not isinstance(value, str):
        abort(400, f"{field} must be a string")
    if len(value) < min_length or (max_length is not None and len(value) > max_length):
        bounds = (
            f"{min_length}-{max_length}" if max_length is not None else f"at least {min_length}"
        )
        abort(400, f"{field} must be {bounds} characters")
    return value
