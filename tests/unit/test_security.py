"""Real Argon2 and signed JWT checks, independent of external services."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

DEMO_PASSWORD = "UnitTestsOnly!2026"


@pytest.fixture
def auth_settings():
    return Settings(
        _env_file=None,
        app_env="test",
        jwt_secret="unit-tests-only-signing-secret-never-use-in-production",
    )


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(DEMO_PASSWORD)


def claims(**changes):
    now = datetime.now(UTC)
    return {
        "sub": "42",
        "ver": 3,
        "iat": now,
        "exp": now + timedelta(minutes=30),
        "iss": "enterprise-agent",
        "aud": "enterprise-agent-api",
        "jti": "unit-test-token",
    } | changes


def sign(payload, settings, *, algorithm="HS256", key=None):
    secret = key if key is not None else settings.jwt_secret.get_secret_value()
    return jwt.encode(payload, secret, algorithm=algorithm)


def assert_unauthenticated(token, settings):
    with pytest.raises(BusinessError) as caught:
        decode_access_token(token, settings)
    assert caught.value.status_code == 401
    assert caught.value.code == "unauthenticated"


def test_argon2_password_hash_uses_salt_and_verifies_the_real_password(password_hash):
    assert password_hash.startswith("$argon2id$")
    assert DEMO_PASSWORD not in password_hash
    assert verify_password(DEMO_PASSWORD, password_hash)
    assert hash_password(DEMO_PASSWORD) != password_hash


def test_password_verification_rejects_wrong_password(password_hash):
    assert not verify_password("wrong-password", password_hash)


@pytest.mark.parametrize("encoded", ["", "not-a-hash", "$argon2id$corrupt"])
def test_password_verification_rejects_corrupt_storage_without_raising(encoded):
    assert not verify_password(DEMO_PASSWORD, encoded)


def test_created_access_token_has_a_finite_lifetime_and_no_identity_authority(auth_settings):
    token = create_access_token(42, 3, auth_settings)
    assert decode_access_token(token, auth_settings) == (42, 3)
    decoded = jwt.decode(
        token,
        auth_settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        audience="enterprise-agent-api",
        issuer="enterprise-agent",
    )
    assert decoded["exp"] - decoded["iat"] == 1800
    assert decoded["jti"]
    assert not {"role", "password", "password_hash"} & decoded.keys()


def test_two_access_tokens_have_distinct_ids(auth_settings):
    first = create_access_token(42, 0, auth_settings)
    second = create_access_token(42, 0, auth_settings)
    assert first != second


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": timedelta(seconds=-1)},
        {"iat": timedelta(minutes=10)},
        {"iss": "other-issuer"},
        {"aud": "other-audience"},
        {"sub": "0"},
        {"sub": "-1"},
        {"sub": "٤٢"},
        {"sub": "forty-two"},
        {"sub": 42},
        {"ver": "3"},
        {"ver": True},
        {"ver": -1},
    ],
    ids=[
        "expired",
        "future-issued",
        "wrong-issuer",
        "wrong-audience",
        "zero-user",
        "negative-user",
        "non-ascii-user",
        "text-user",
        "numeric-subject",
        "string-version",
        "boolean-version",
        "negative-version",
    ],
)
def test_invalid_signed_claims_are_not_accepted(changes, auth_settings):
    # Resolve offsets at execution; collection may precede this test by minutes.
    now = datetime.now(UTC)
    resolved = {
        key: now + value if isinstance(value, timedelta) else value
        for key, value in changes.items()
    }
    assert_unauthenticated(sign(claims(**resolved), auth_settings), auth_settings)


@pytest.mark.parametrize("missing", ["sub", "ver", "iat", "exp", "iss", "aud", "jti"])
def test_required_claims_cannot_be_omitted(missing, auth_settings):
    payload = claims()
    del payload[missing]
    assert_unauthenticated(sign(payload, auth_settings), auth_settings)


def test_wrong_signing_key_is_not_accepted(auth_settings):
    token = sign(
        claims(), auth_settings, key="another-tests-only-secret-at-least-thirty-two-characters"
    )
    assert_unauthenticated(token, auth_settings)


def test_payload_tampering_invalidates_the_signature(auth_settings):
    token = sign(claims(), auth_settings)
    header, _, signature = token.split(".")
    replacement = sign(claims(sub="99"), auth_settings).split(".")[1]
    assert_unauthenticated(".".join([header, replacement, signature]), auth_settings)


@pytest.mark.parametrize("algorithm", ["HS384", "none"])
def test_only_configured_hs256_tokens_are_accepted(algorithm, auth_settings):
    token = jwt.encode(
        claims(),
        None if algorithm == "none" else auth_settings.jwt_secret.get_secret_value(),
        algorithm=algorithm,
    )
    assert_unauthenticated(token, auth_settings)


@pytest.mark.parametrize("token", ["", "not.a.jwt", "Bearer invalid", "a.b.c.d"])
def test_malformed_tokens_are_rejected_without_parser_details(token, auth_settings):
    assert_unauthenticated(token, auth_settings)


def test_unconfigured_authentication_fails_explicitly():
    settings = Settings(_env_file=None, app_env="test")
    with pytest.raises(BusinessError) as creation:
        create_access_token(42, 0, settings)
    with pytest.raises(BusinessError) as decoding:
        decode_access_token("untrusted-token", settings)
    assert creation.value.code == decoding.value.code == "authentication_unavailable"
    assert creation.value.status_code == decoding.value.status_code == 503
