"""Random secrets (database passwords, data-encryption key), one copy per allowed service."""

import base64
import os
import secrets

from .files import write
from .layout import SECRET_AUDIENCE, master, root


def _generate_secret(name: str) -> str:
    if name == "data_encryption_key":
        return base64.b64encode(os.urandom(32)).decode()  # AES-256
    return secrets.token_urlsafe(48)


def provision_secrets() -> None:
    (root() / "db-manager" / "postgres_password").unlink(missing_ok=True)
    for name, audience in SECRET_AUDIENCE.items():
        copy = master() / name
        if not copy.exists():
            write(copy, _generate_secret(name))
        value = copy.read_text()
        for service in audience:
            write(root() / service / name, value)
