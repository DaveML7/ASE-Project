"""The request id that ties one user request to the log lines of every service."""

import re
import uuid

from flask import g, has_request_context, request

REQUEST_ID_HEADER = "X-Request-ID"
VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def assign_request_id() -> None:
    """Keep a well-formed id from the caller (gateway) or create one."""
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    g.request_id = supplied if VALID_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex[:16]


def current_request_id() -> str:
    return getattr(g, "request_id", "-")


def context_headers() -> dict:
    """Headers that follow the current user request through every outgoing call."""
    if has_request_context() and getattr(g, "request_id", None):
        return {REQUEST_ID_HEADER: g.request_id}
    return {}
