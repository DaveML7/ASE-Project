"""Transparent column encryption for the db-manager (AES-256-GCM).

Sensitive columns are encrypted before they reach PostgreSQL and decrypted when
read back, so a database dump or a stolen volume never exposes them without the
key. The key lives only in the secrets volume, never in the database.

Stored format: ``enc:v1:`` + base64(nonce || ciphertext || tag). The column
identity (``table.column``) is bound as associated data, so a ciphertext copied
into another column or table fails authentication instead of decrypting.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc:v1:"
NONCE_BYTES = 12
KEY_BYTES = 32


class CryptoError(RuntimeError):
    """Encryption key invalid or ciphertext failed authentication."""


class FieldCipher:
    def __init__(self, key: bytes):
        if len(key) != KEY_BYTES:
            raise CryptoError(f"data encryption key must be {KEY_BYTES} bytes (AES-256)")
        self._aead = AESGCM(key)

    @classmethod
    def from_base64(cls, encoded: str) -> "FieldCipher":
        try:
            return cls(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise CryptoError("data encryption key is not valid base64") from exc

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), context.encode())
        return PREFIX + base64.b64encode(nonce + sealed).decode()

    def decrypt(self, token: str, context: str) -> str:
        if not token.startswith(PREFIX):
            raise CryptoError(f"{context} is not stored encrypted")
        try:
            raw = base64.b64decode(token.removeprefix(PREFIX), validate=True)
            nonce, sealed = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
            return self._aead.decrypt(nonce, sealed, context.encode()).decode()
        except (binascii.Error, InvalidTag, ValueError) as exc:
            raise CryptoError(f"cannot decrypt {context}") from exc
