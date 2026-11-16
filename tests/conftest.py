"""Shared fixtures: a throw-away key directory standing in for /run/app-config."""

import pytest
from ase_common import clear_cache
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def private_pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def public_pem(key) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


class KeyDir:
    def __init__(self, path):
        self.path = path
        self.user_key = ed25519.Ed25519PrivateKey.generate()
        (path / "jwt_private.pem").write_bytes(private_pem(self.user_key))
        (path / "jwt_public.pem").write_bytes(public_pem(self.user_key))

    def identity(self, name: str):
        """Become service `name`: its private key becomes service.key."""
        key = ed25519.Ed25519PrivateKey.generate()
        (self.path / "service.key").write_bytes(private_pem(key))
        clear_cache()
        return key

    def grant(self, caller: str, key) -> None:
        """Hand the callee the public key of `caller` (what the init job does)."""
        (self.path / f"{caller}.pub").write_bytes(public_pem(key))
        clear_cache()


@pytest.fixture
def key_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SERVICE_NAME", "tester")
    keys = KeyDir(tmp_path)
    keys.identity("tester")
    clear_cache()
    yield keys
    clear_cache()


def forge_hs256(claims: dict, secret: bytes) -> str:
    """Hand-built HS256 token (PyJWT refuses to sign with a PEM key, an attacker would not)."""
    import base64
    import hashlib
    import hmac
    import json

    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    signature = b64(hmac.new(secret, head + b"." + body, hashlib.sha256).digest())
    return (head + b"." + body + b"." + signature).decode()
