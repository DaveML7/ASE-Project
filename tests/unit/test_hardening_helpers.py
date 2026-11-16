import jwt
import pytest
from ase_common import RateLimiter, decode_token, issue_token
from ase_common.auth import JWT_AUDIENCE, JWT_ISSUER
from conftest import forge_hs256
from cryptography.hazmat.primitives.asymmetric import ed25519


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


def test_token_carries_the_required_claims(key_dir):
    payload = decode_token(issue_token("p1", "ash"))
    assert payload["iss"] == JWT_ISSUER and payload["aud"] == JWT_AUDIENCE and payload["jti"]


def test_each_token_has_its_own_id(key_dir):
    assert decode_token(issue_token("p1", "a"))["jti"] != decode_token(issue_token("p1", "a"))["jti"]


def test_token_for_another_audience_issuer_or_without_jti_is_rejected(key_dir):
    base = {"sub": "p1", "exp": 4102444800, "jti": "x"}
    for claims in (
        {**base, "iss": JWT_ISSUER, "aud": "someone-else"},
        {**base, "iss": "evil", "aud": JWT_AUDIENCE},
        {"sub": "p1", "exp": 4102444800, "iss": JWT_ISSUER, "aud": JWT_AUDIENCE},
    ):
        with pytest.raises(jwt.PyJWTError):
            decode_token(jwt.encode(claims, key_dir.user_key, algorithm="EdDSA"))


def test_token_signed_by_another_key_is_rejected(key_dir):
    claims = {"sub": "p1", "exp": 4102444800, "jti": "x", "iss": JWT_ISSUER, "aud": JWT_AUDIENCE}
    with pytest.raises(jwt.PyJWTError):
        decode_token(jwt.encode(claims, ed25519.Ed25519PrivateKey.generate(), algorithm="EdDSA"))


def test_unsigned_and_hs256_confusion_tokens_are_rejected(key_dir):
    claims = {"sub": "p1", "exp": 4102444800, "jti": "x", "iss": JWT_ISSUER, "aud": JWT_AUDIENCE}
    with pytest.raises(jwt.PyJWTError):
        decode_token(jwt.encode(claims, None, algorithm="none"))
    public = (key_dir.path / "jwt_public.pem").read_bytes()
    with pytest.raises(jwt.PyJWTError):  # algorithm confusion: HMAC with the public key as secret
        decode_token(forge_hs256(claims, public))
