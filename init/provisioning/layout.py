"""Who gets what: the directories, the services and the key/secret distribution rules."""

import datetime
import os
from pathlib import Path


def root() -> Path:
    return Path(os.getenv("RUNTIME_DIR", "/runtime"))


def master() -> Path:
    """Never mounted by any service: the CA key and the master copy of every private key."""
    return root() / ".master"


DOMAIN_SERVICES = ["auth", "players", "monsters", "teams", "encounters", "history"]
TLS_SERVICES = ["gateway", *DOMAIN_SERVICES, "db-manager", "db"]

# secret name -> services whose directory receives a copy (least privilege)
SECRET_AUDIENCE = {
    "postgres_password": ["db"],
    "db_app_password": ["db", "db-manager"],
    "data_encryption_key": ["db-manager"],
}

# Every service that calls another one signs with its own key (the db-manager only verifies).
CALLERS = ["gateway", *DOMAIN_SERVICES]
# callee -> the only services whose PUBLIC key it receives, i.e. who may call it
ALLOWED_CALLERS = {
    "db-manager": DOMAIN_SERVICES,
    "players": ["auth", "teams", "encounters"],
    "teams": ["encounters"],
    "auth": ["gateway"],
}
# who verifies user tokens (the public key); monsters serves only public routes
JWT_VERIFIERS = ["gateway", "auth", "players", "teams", "encounters", "history"]

CA_YEARS = 10
LEAF_DAYS = 825
RENEW_BEFORE = datetime.timedelta(days=30)
