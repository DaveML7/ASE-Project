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
    tls = {"tls.crt", "tls.key", "ca.crt"}
    assert names(root / "gateway") == tls | {"service.key", "jwt_public.pem"}
    assert names(root / "monsters") == tls | {"service.key"}  # public catalogue: no user-token key
    assert names(root / "auth") == tls | {"service.key", "jwt_public.pem", "jwt_private.pem", "gateway.pub"}
    assert names(root / "teams") == tls | {"service.key", "jwt_public.pem", "encounters.pub"}
    assert names(root / "db-manager") == tls | {
        "data_encryption_key", "db_app_password",
        "auth.pub", "players.pub", "monsters.pub", "teams.pub", "encounters.pub", "history.pub",
    }
    assert names(root / "db") == tls | {"postgres_password", "db_app_password"}


def test_no_shared_secret_exists_any_more(provisioned):
    _, root = provisioned
    everything = {p.name for p in root.rglob("*") if p.is_file()}
    assert not {"jwt_secret", "internal_api_key"} & everything


def test_only_the_auth_service_can_sign_user_tokens(provisioned):
    _, root = provisioned
    holders = [d.name for d in root.iterdir() if not d.name.startswith(".") and (d / "jwt_private.pem").exists()]
    assert holders == ["auth"]


def test_a_service_receives_public_keys_only_for_its_allowed_callers(provisioned):
    module, root = provisioned
    for callee, callers in module.ALLOWED_CALLERS.items():
        have = {p.stem for p in (root / callee).glob("*.pub")}
        assert have == set(callers)
    assert not list((root / "players").glob("monsters.pub")), "monsters may not call players"


def test_public_keys_match_the_callers_private_keys(provisioned):
    from cryptography.hazmat.primitives import serialization

    _, root = provisioned
    private = serialization.load_pem_private_key((root / "encounters" / "service.key").read_bytes(), password=None)
    public = serialization.load_pem_public_key((root / "players" / "encounters.pub").read_bytes())
    expected = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) == expected


def test_secrets_are_random_and_distinct(provisioned):
    _, root = provisioned
    app_password = (root / "db-manager" / "db_app_password").read_text()
    assert app_password == (root / "db" / "db_app_password").read_text()
    assert app_password != (root / "db" / "postgres_password").read_text()
    assert app_password != (root / "db-manager" / "data_encryption_key").read_text()
    assert len(app_password) >= 32
    keys = {(root / s / "service.key").read_bytes() for s in ("gateway", "auth", "players", "teams")}
    assert len(keys) == 4, "every service must have its own identity key"


def test_data_key_and_ca_key_are_not_widely_distributed(provisioned):
    module, root = provisioned
    holders = [d.name for d in root.iterdir() if not d.name.startswith(".") and (d / "data_encryption_key").exists()]
    assert holders == ["db-manager"]
    service_dirs = [d for d in root.iterdir() if not d.name.startswith(".")]
    assert not [d for d in service_dirs if (d / "ca.key").exists()], "the CA key must stay in .master"
    assert (root / ".master" / "ca.key").exists()


def test_rerun_keeps_existing_keys(provisioned):
    module, root = provisioned
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    module.main()
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("service", ["auth", "db"])
def test_certificates_chain_to_the_ca_and_name_the_service(provisioned, service):
    _, root = provisioned
    ca = x509.load_pem_x509_certificate((root / service / "ca.crt").read_bytes())
    leaf = x509.load_pem_x509_certificate((root / service / "tls.crt").read_bytes())
    leaf.verify_directly_issued_by(ca)
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert service in san.get_values_for_type(x509.DNSName)
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    assert isinstance(ca.public_key(), ec.EllipticCurvePublicKey)
