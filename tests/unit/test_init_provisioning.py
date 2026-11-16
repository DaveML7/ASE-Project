"""The first-start job: per-service isolation, idempotence and a valid certificate chain."""

import importlib.util
import pathlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

INIT = pathlib.Path(__file__).parents[2] / "init" / "init.py"


@pytest.fixture
def provisioned(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("init_job", INIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
    return module, tmp_path


def names(directory):
    return {path.name for path in directory.iterdir()}


def test_every_service_gets_only_what_it_needs(provisioned):
    _, root = provisioned
    assert names(root / "gateway") == {"tls.crt", "tls.key", "ca.crt", "jwt_secret", "internal_api_key"}
    assert names(root / "db-manager") == {
        "tls.crt", "tls.key", "ca.crt", "internal_api_key", "postgres_password", "data_encryption_key",
    }
    assert names(root / "db") == {"postgres_password"}


def test_data_key_and_ca_key_are_not_widely_distributed(provisioned):
    module, root = provisioned
    holders = [d.name for d in root.iterdir() if not d.name.startswith(".") and (d / "data_encryption_key").exists()]
    assert holders == ["db-manager"]
    service_dirs = [d for d in root.iterdir() if not d.name.startswith(".")]
    assert not [d for d in service_dirs if (d / "ca.key").exists()], "the CA key must stay in .master"
    assert (root / ".master" / "ca.key").exists()


def test_secrets_are_random_strong_and_consistent(provisioned):
    _, root = provisioned
    assert (root / "gateway" / "jwt_secret").read_text() == (root / "auth" / "jwt_secret").read_text()
    assert len((root / "gateway" / "jwt_secret").read_text()) >= 32
    assert (root / "db" / "postgres_password").read_text() == (root / "db-manager" / "postgres_password").read_text()
    assert (root / "gateway" / "jwt_secret").read_text() != (root / "gateway" / "internal_api_key").read_text()


def test_rerun_keeps_existing_keys(provisioned):
    module, root = provisioned
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    module.main()
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_certificates_chain_to_the_ca_and_name_the_service(provisioned):
    _, root = provisioned
    ca = x509.load_pem_x509_certificate((root / "auth" / "ca.crt").read_bytes())
    leaf = x509.load_pem_x509_certificate((root / "auth" / "tls.crt").read_bytes())
    leaf.verify_directly_issued_by(ca)
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "auth" in san.get_values_for_type(x509.DNSName)
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    assert isinstance(ca.public_key(), ec.EllipticCurvePublicKey)
