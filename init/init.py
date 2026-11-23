"""First-start provisioning of secrets and TLS material.

Creates, only when missing:

* random secrets (database passwords and the AES-256 data-encryption key);
* Ed25519 key pairs: one per calling service (service identity) and one for the user
  tokens. Private keys go only to their owner; callees receive only the PUBLIC keys of
  the services allowed to call them;
* a private CA and one TLS certificate per service (SAN = service DNS name).

Everything is written into ONE DIRECTORY PER SERVICE (`/runtime/<service>/`) and each
container mounts only its own directory, so a compromised service can read neither the other
services' keys nor secrets it does not need. The CA private key and the master copies live in
`/runtime/.master`, which no service mounts. Re-running keeps every existing file.
"""

from provisioning.identity_keys import provision_keys
from provisioning.layout import (
    ALLOWED_CALLERS,
    CALLERS,
    DOMAIN_SERVICES,
    JWT_VERIFIERS,
    master,
    root,
)
from provisioning.secrets_store import provision_secrets
from provisioning.tls import provision_certificates

__all__ = ["ALLOWED_CALLERS", "CALLERS", "DOMAIN_SERVICES", "JWT_VERIFIERS", "main"]


def main() -> None:
    root().mkdir(parents=True, exist_ok=True)
    master().mkdir(parents=True, exist_ok=True)
    master().chmod(0o700)
    provision_secrets()
    provision_keys()
    provision_certificates()
    print(
        "provisioned:",
        ", ".join(sorted(p.name for p in root().iterdir() if not p.name.startswith("."))),
    )


if __name__ == "__main__":
    main()
