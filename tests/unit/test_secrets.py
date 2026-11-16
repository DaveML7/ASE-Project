import pytest
from ase_common.secrets import SecretError, read_secret

GOOD = "k" * 48


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    for name in ("DEMO_SECRET", "DEMO_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)
    read_secret.cache_clear()
    yield
    read_secret.cache_clear()


def test_missing_secret_is_an_error():
    with pytest.raises(SecretError, match="not configured"):
        read_secret("DEMO_SECRET")


def test_reads_from_environment(monkeypatch):
    monkeypatch.setenv("DEMO_SECRET", GOOD)
    assert read_secret("DEMO_SECRET") == GOOD


def test_file_takes_precedence_and_is_stripped(monkeypatch, tmp_path):
    secret_file = tmp_path / "s"
    secret_file.write_text(GOOD + "\n")
    monkeypatch.setenv("DEMO_SECRET", "x" * 48)
    monkeypatch.setenv("DEMO_SECRET_FILE", str(secret_file))
    assert read_secret("DEMO_SECRET") == GOOD


def test_unreadable_file_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_SECRET_FILE", str(tmp_path / "missing"))
    with pytest.raises(SecretError, match="cannot read"):
        read_secret("DEMO_SECRET")


def test_short_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("DEMO_SECRET", "short")
    with pytest.raises(SecretError, match="at least"):
        read_secret("DEMO_SECRET")


@pytest.mark.parametrize("value", ["change-me-too" + "x" * 40, "Password" + "1" * 40])
def test_placeholder_secret_is_rejected(monkeypatch, value):
    monkeypatch.setenv("DEMO_SECRET", value)
    with pytest.raises(SecretError, match="placeholder"):
        read_secret("DEMO_SECRET")
