"""Decorators that remove the boilerplate every endpoint used to repeat."""

from functools import wraps

from flask import g

from .parsing import json_object


def with_json_body(view):
    """Give the view the request body as a JSON object (`body=`); anything else is a 400."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        return view(*args, body=json_object(), **kwargs)

    return wrapper


def owned_resource(loader, *, not_found: str, key: str):
    """Load a resource by the id in the URL and only continue if the caller owns it.

    `loader(resource_id)` returns the row or None. A row that is missing or belongs to
    another player is the same 404, so ids cannot be probed. The row is passed to the
    view as `key=`. Must sit below `@require_jwt`.
    """
    from .http import error

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            resource_id = kwargs.pop(f"{key}_id")
            row = loader(resource_id)
            if row is None or str(row["player_id"]) != str(g.player_id):
                return error(404, not_found)
            return view(*args, **{key: row}, **kwargs)

        return wrapper

    return decorator
