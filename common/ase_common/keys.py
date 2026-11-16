"""Asymmetric keys delivered by the init job into /run/app-config.

* ``service.key``     private Ed25519 key of THIS service (signs its service tokens)
* ``<caller>.pub``    public keys of the services allowed to call this one
* ``jwt_private.pem`` private key that signs user tokens (only the auth service has it)
* ``jwt_public.pem``  public key that verifies user tokens (everyone who checks them)

A service can therefore verify tokens but never forge them: it only holds public keys
of its callers and the user-token public key.
"""

import os
from functools import cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization


class KeyError_(RuntimeError):
    """A required key file is missing or unreadable."""


def config_dir() -> Path:
    return Path(os.getenv("APP_CONFIG_DIR", "/run/app-config"))


def _read(name: str) -> bytes:
    try:
        return (config_dir() / name).read_bytes()
    except OSError as exc:
        raise KeyError_(f"key file {name} is not available: {exc.strerror}") from exc


@cache
def _load_private(name: str):
    return serialization.load_pem_private_key(_read(name), password=None)


@cache
def _load_public(name: str):
    return serialization.load_pem_public_key(_read(name))


def service_private_key():
    return _load_private("service.key")


def jwt_private_key():
    return _load_private("jwt_private.pem")


def jwt_public_key():
    return _load_public("jwt_public.pem")


def peer_public_key(caller: str):
    """Public key of an allowed caller; raises KeyError_ if init did not grant it."""
    if "/" in caller or caller.startswith("."):
        raise KeyError_("invalid caller name")
    return _load_public(f"{caller}.pub")


def clear_cache() -> None:
    _load_private.cache_clear()
    _load_public.cache_clear()
