"""A private CA and one TLS certificate per service."""

import datetime
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .files import pem_private, write
from .layout import CA_YEARS, LEAF_DAYS, RENEW_BEFORE, TLS_SERVICES, master, root


def _load_ca():
    key_path, cert_path = master() / "ca.key", master() / "ca.crt"
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
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    write(key_path, pem_private(key))
    write(cert_path, certificate.public_bytes(serialization.Encoding.PEM))
    return key, certificate


def _needs_certificate(service: str) -> bool:
    path = root() / service / "tls.crt"
    if not path.exists() or not (root() / service / "tls.key").exists():
        return True
    expires = x509.load_pem_x509_certificate(path.read_bytes()).not_valid_after_utc
    return expires - datetime.datetime.now(datetime.timezone.utc) < RENEW_BEFORE


def provision_certificates() -> None:
    ca_key, ca_cert = _load_ca()
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    for service in TLS_SERVICES:
        write(root() / service / "ca.crt", ca_pem)
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
        write(root() / service / "tls.crt", certificate.public_bytes(serialization.Encoding.PEM))
        write(root() / service / "tls.key", pem_private(key))
