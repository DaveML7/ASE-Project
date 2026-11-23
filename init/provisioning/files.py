"""Writing and serialising key material."""

from pathlib import Path

from cryptography.hazmat.primitives import serialization


def write(path: Path, data: bytes | str) -> None:
    """Atomic write, world-readable: only the owning service mounts this directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data.encode() if isinstance(data, str) else data)
    temporary.chmod(0o644)
    temporary.replace(path)


def pem_private(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def pem_public(key) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
