import base64
import os

import pytest

from dbmanager.crypto import PREFIX, CryptoError, FieldCipher


@pytest.fixture
def cipher():
    return FieldCipher(os.urandom(32))


def test_round_trip(cipher):
    token = cipher.encrypt("scrypt:hash", "credentials.password_hash")
    assert token.startswith(PREFIX)
    assert "scrypt" not in token
    assert cipher.decrypt(token, "credentials.password_hash") == "scrypt:hash"


def test_encryption_is_randomised(cipher):
    first = cipher.encrypt("same", "t.c")
    assert first != cipher.encrypt("same", "t.c")


def test_ciphertext_is_bound_to_its_column(cipher):
    token = cipher.encrypt("value", "credentials.password_hash")
    with pytest.raises(CryptoError):
        cipher.decrypt(token, "players.username")


def test_tampered_ciphertext_is_rejected(cipher):
    token = cipher.encrypt("value", "t.c")
    raw = bytearray(base64.b64decode(token.removeprefix(PREFIX)))
    raw[-1] ^= 1
    with pytest.raises(CryptoError):
        cipher.decrypt(PREFIX + base64.b64encode(bytes(raw)).decode(), "t.c")


def test_wrong_key_is_rejected(cipher):
    token = cipher.encrypt("value", "t.c")
    with pytest.raises(CryptoError):
        FieldCipher(os.urandom(32)).decrypt(token, "t.c")


def test_plaintext_in_the_database_is_refused(cipher):
    with pytest.raises(CryptoError):
        cipher.decrypt("not-encrypted", "t.c")


@pytest.mark.parametrize("size", [0, 16, 31, 33])
def test_key_must_be_aes_256(size):
    with pytest.raises(CryptoError):
        FieldCipher(os.urandom(size))


def test_key_must_be_valid_base64():
    with pytest.raises(CryptoError):
        FieldCipher.from_base64("***not base64***")
