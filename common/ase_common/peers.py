"""Calls from one microservice to another."""

import threading

import requests

from .config import SERVICE_URLS
from .resilience import guarded_request, pooled_session
from .service_identity import SERVICE_HEADER, service_token

_peer_session: requests.Session | None = None
_peer_lock = threading.Lock()


def peer_session() -> requests.Session:
    global _peer_session
    with _peer_lock:
        if _peer_session is None:
            _peer_session = pooled_session()
        return _peer_session


def call_service(
    service: str,
    method: str,
    path: str,
    user_token: str | None = None,
    timeout=None,
    **kwargs,
):
    """Call a peer service over HTTPS, signed as this service.

    `user_token` forwards the end user's own JWT: the callee verifies it itself, so a
    service can only act on behalf of a player whose token it actually holds.
    """
    headers = {SERVICE_HEADER: service_token(service)}
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
    return guarded_request(
        peer_session(),
        service,
        method,
        f"{SERVICE_URLS[service].rstrip('/')}{path}",
        timeout=timeout,
        headers=headers,
        **kwargs,
    )
