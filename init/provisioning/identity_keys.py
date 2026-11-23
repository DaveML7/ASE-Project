"""Ed25519 identities: who may call whom, and who may sign or only verify user tokens."""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from .files import pem_private, pem_public, write
from .layout import ALLOWED_CALLERS, CALLERS, JWT_VERIFIERS, master, root


def _master_ed25519(name: str):
    """Load (or create once) the Ed25519 private key kept in .master."""
    path = master() / "keys" / f"{name}.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = ed25519.Ed25519PrivateKey.generate()
    write(path, pem_private(key))
    return key


def provision_keys() -> None:
    for service in CALLERS:
        write(root() / service / "service.key", pem_private(_master_ed25519(service)))
    for callee, callers in ALLOWED_CALLERS.items():
        for caller in callers:
            write(root() / callee / f"{caller}.pub", pem_public(_master_ed25519(caller)))
    user_key = _master_ed25519("jwt")
    write(root() / "auth" / "jwt_private.pem", pem_private(user_key))
    for service in JWT_VERIFIERS:
        write(root() / service / "jwt_public.pem", pem_public(user_key))
