"""Transparent encryption of sensitive columns (the key is read once, at start-up)."""

from ase_common import read_secret

from .crypto import FieldCipher
from .policy import TABLES

cipher = FieldCipher.from_base64(read_secret("DATA_ENCRYPTION_KEY", min_length=44))


def protect(table: str, values: dict) -> dict:
    """Encrypt the sensitive columns of a row about to be written."""
    protected = dict(values)
    for column in TABLES[table].encrypted & protected.keys():
        protected[column] = cipher.encrypt(protected[column], f"{table}.{column}")
    return protected


def reveal(table: str, row: dict | None) -> dict | None:
    """Decrypt the sensitive columns of a row read from the database."""
    if row is None:
        return None
    revealed = dict(row)
    for column in TABLES[table].encrypted & revealed.keys():
        revealed[column] = cipher.decrypt(revealed[column], f"{table}.{column}")
    return revealed
