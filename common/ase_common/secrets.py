"""Secret loading with no built-in defaults.

A secret is read from the file named by ``<NAME>_FILE`` (the Docker-secrets
convention) and, only if that variable is unset, from the plain ``<NAME>``
environment variable. A missing, too-short or placeholder value is an error:
the service refuses to start instead of silently running with a guessable key.
"""

import os
from functools import cache
from pathlib import Path

PLACEHOLDER_PREFIXES = ("change-me", "changeme", "default", "secret", "password", "example")


class SecretError(RuntimeError):
    """A required secret is missing or unusable."""


@cache
def read_secret(name: str, min_length: int = 32) -> str:
    path = os.getenv(f"{name}_FILE")
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SecretError(f"cannot read {name}_FILE ({path}): {exc.strerror}") from exc
    else:
        value = os.getenv(name, "").strip()

    if not value:
        raise SecretError(f"{name} is not configured: set {name}_FILE or {name}")
    if len(value) < min_length:
        raise SecretError(f"{name} must be at least {min_length} characters long")
    if value.lower().startswith(PLACEHOLDER_PREFIXES):
        raise SecretError(f"{name} still holds a placeholder value")
    return value


def internal_api_key() -> str:
    """Shared key that authenticates service-to-service calls."""
    return read_secret("INTERNAL_API_KEY")


def jwt_secret() -> str:
    """HS256 signing key for the access tokens."""
    return read_secret("JWT_SECRET")
