"""First-start provisioning of secrets and TLS material.

Creates, only when missing:

* random secrets (database password and the AES-256 data-encryption key);
* Ed25519 key pairs: one per calling service (service identity) and one for the user
  tokens. Private keys go only to their owner; callees receive only the PUBLIC keys of
  the services allowed to call them;
* a private CA and one TLS certificate per service (SAN = service DNS name).

Everything is written into ONE DIRECTORY PER SERVICE (``/runtime/<service>/``)
and each container mounts only its own directory (compose ``subpath``), so a
compromised service can read neither the other services' TLS keys nor secrets it
does not need (for example only db-manager sees the data-encryption key). The CA
private key and the master copies live in ``/runtime/.master``, which no service
mounts.

Re-running is safe: existing files are kept, so restarts never rotate keys
(rotating the data key would make encrypted columns unreadable).
"""

import base64
import datetime
import ipaddress
import os
import secrets
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(os.getenv("RUNTIME_DIR", "/runtime"))
MASTER = ROOT / ".master"

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


def _write(path: Path, data: bytes | str) -> None:
    """Atomic write, world-readable: only the owning service mounts this directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data.encode() if isinstance(data, str) else data)
    temporary.chmod(0o644)
    temporary.replace(path)


def _generate_secret(name: str) -> str:
    if name == "data_encryption_key":
        return base64.b64encode(os.urandom(32)).decode()  # AES-256
    return secrets.token_urlsafe(48)


def provision_secrets() -> None:
    (ROOT / "db-manager" / "postgres_password").unlink(missing_ok=True)
    for name, audience in SECRET_AUDIENCE.items():
        master = MASTER / name
        if not master.exists():
            _write(master, _generate_secret(name))
        value = master.read_text()
        for service in audience:
            _write(ROOT / service / name, value)


def _public_pem(key) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _master_ed25519(name: str):
    """Load (or create once) the Ed25519 private key kept in .master."""
    path = MASTER / "keys" / f"{name}.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = ed25519.Ed25519PrivateKey.generate()
    _write(path, _pem_key(key))
    return key


def provision_keys() -> None:
    for service in CALLERS:
        _write(ROOT / service / "service.key", _pem_key(_master_ed25519(service)))
    for callee, callers in ALLOWED_CALLERS.items():
        for caller in callers:
            _write(ROOT / callee / f"{caller}.pub", _public_pem(_master_ed25519(caller)))
    user_key = _master_ed25519("jwt")
    _write(ROOT / "auth" / "jwt_private.pem", _pem_key(user_key))
    for service in JWT_VERIFIERS:
        _write(ROOT / service / "jwt_public.pem", _public_pem(user_key))


def _pem_key(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _load_ca():
    key_path, cert_path = MASTER / "ca.key", MASTER / "ca.crt"
    if key_path.exists() and cert_path.exists():
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        return key, x509.load_pem_x509_certificate(cert_path.read_bytes())

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "monster-tamers-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365 * CA_YEARS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    _write(key_path, _pem_key(key))
    _write(cert_path, certificate.public_bytes(serialization.Encoding.PEM))
    return key, certificate


def _needs_certificate(service: str) -> bool:
    path = ROOT / service / "tls.crt"
    if not path.exists() or not (ROOT / service / "tls.key").exists():
        return True
    expires = x509.load_pem_x509_certificate(path.read_bytes()).not_valid_after_utc
    return expires - datetime.datetime.now(datetime.timezone.utc) < RENEW_BEFORE


def provision_certificates() -> None:
    ca_key, ca_cert = _load_ca()
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    for service in TLS_SERVICES:
        _write(ROOT / service / "ca.crt", ca_pem)
        if not _needs_certificate(service):
            continue
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, service)]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=LEAF_DAYS))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName(service),
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        _write(ROOT / service / "tls.crt", certificate.public_bytes(serialization.Encoding.PEM))
        _write(ROOT / service / "tls.key", _pem_key(key))


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    MASTER.mkdir(parents=True, exist_ok=True)
    MASTER.chmod(0o700)
    provision_secrets()
    provision_keys()
    provision_certificates()
    print("provisioned:", ", ".join(sorted(p.name for p in ROOT.iterdir() if not p.name.startswith("."))))


if __name__ == "__main__":
    main()
