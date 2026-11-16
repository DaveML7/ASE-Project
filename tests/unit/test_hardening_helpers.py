import jwt
import pytest
from ase_common import RateLimiter, decode_token, issue_token
from ase_common.auth import JWT_AUDIENCE, JWT_ISSUER
from ase_common.secrets import jwt_secret


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    from ase_common import secrets as module

    for name in ("INTERNAL_API_KEY", "JWT_SECRET"):
        monkeypatch.setenv(name, "h" * 48)
    module.read_secret.cache_clear()


def test_rate_limiter_allows_the_limit_then_blocks_then_recovers():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    assert [limiter.check("k", 3) for _ in range(3)] == [0, 0, 0]
    assert limiter.check("k", 3) > 0
    assert limiter.check("other", 3) == 0
    now[0] = 61
    assert limiter.check("k", 3) == 0


def test_rate_limiter_memory_is_bounded(monkeypatch):
    from ase_common import ratelimit

    monkeypatch.setattr(ratelimit, "MAX_KEYS", 10)
    limiter = RateLimiter()
    for index in range(200):
        limiter.check(f"key{index}", 5)
    assert len(limiter._hits) <= 10


def test_token_carries_and_requires_issuer_and_audience():
    payload = decode_token(issue_token("p1", "ash"))
    assert payload["iss"] == JWT_ISSUER and payload["aud"] == JWT_AUDIENCE


def test_token_for_another_audience_or_issuer_is_rejected():
    base = {"sub": "p1", "exp": 4102444800}
    for claims in (
        {**base, "iss": JWT_ISSUER, "aud": "someone-else"},
        {**base, "iss": "evil", "aud": JWT_AUDIENCE},
        base,
    ):
        with pytest.raises(jwt.PyJWTError):
            decode_token(jwt.encode(claims, jwt_secret(), algorithm="HS256"))


def test_unsigned_token_is_rejected():
    forged = jwt.encode({"sub": "p1", "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "exp": 4102444800}, None, algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        decode_token(forged)
