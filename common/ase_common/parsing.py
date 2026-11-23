"""Reading and validating what a client sent: the same rules and the same answers everywhere."""

from flask import abort, request


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
