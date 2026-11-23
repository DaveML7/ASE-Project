"""Shared library of the Monster Tamers microservices."""

from .app import create_app
from .concurrency import run_concurrently
from .config import CA_CERT, JWT_TTL_SECONDS, SERVICE_URLS
from .db_client import DB, DbError
from .decorators import owned_resource, with_json_body
from .http import error
from .http_cache import cacheable_json
from .keys import KeyError_, clear_cache, jwt_public_key, service_private_key
from .parsing import json_object, parse_limit, require_string
from .peers import call_service, peer_session
from .ratelimit import RateLimiter
from .request_context import REQUEST_ID_HEADER
from .resilience import CircuitBreaker, CircuitOpen, guarded_request
from .secrets import SecretError, read_secret
from .serializers import collection_entry, monster_public
from .service_identity import require_service, service_token
from .tokens import bearer_token, decode_token, issue_token, require_jwt

__all__ = [
    "CA_CERT",
    "DB",
    "JWT_TTL_SECONDS",
    "REQUEST_ID_HEADER",
    "SERVICE_URLS",
    "CircuitBreaker",
    "CircuitOpen",
    "DbError",
    "KeyError_",
    "RateLimiter",
    "SecretError",
    "bearer_token",
    "cacheable_json",
    "call_service",
    "clear_cache",
    "collection_entry",
    "create_app",
    "decode_token",
    "error",
    "guarded_request",
    "issue_token",
    "json_object",
    "jwt_public_key",
    "monster_public",
    "owned_resource",
    "parse_limit",
    "peer_session",
    "read_secret",
    "require_jwt",
    "require_service",
    "require_string",
    "run_concurrently",
    "service_private_key",
    "service_token",
    "with_json_body",
]
