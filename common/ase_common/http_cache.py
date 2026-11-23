"""HTTP caching for data that rarely changes: Cache-Control plus a validator (ETag).

A browser (or any client) that already holds the answer sends `If-None-Match`; if nothing
changed it gets an empty `304 Not Modified` instead of the whole body.
"""

from flask import jsonify, request


def cacheable_json(payload, max_age: int, *, public: bool):
    response = jsonify(payload)
    response.headers["Cache-Control"] = f"{'public' if public else 'private'}, max-age={max_age}"
    response.add_etag()
    return response.make_conditional(request)
