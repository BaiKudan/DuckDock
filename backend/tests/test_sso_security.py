"""SSO security hardening (L0-SEC-SSO).

Three sub-fixes are exercised here:

1. Secret encryption at rest — ``SSOProviderConfig.client_secret`` and
   ``.ldap_bind_password`` are Fernet-encrypted in the DB column (same mechanism
   as ``credential_service``), transparently decrypted on read, with legacy
   plaintext rows still readable.
2. OIDC ``id_token`` signature verification — the id_token is verified against
   the provider JWKS (signature + issuer + audience + expiry); a token signed by
   a different key / expired / wrong-aud is rejected. No unverified fallback.
3. SSRF guard on all outbound SSO HTTP — discovery / token / jwks / userinfo
   calls go through the shared ``validate_external_url`` guard so a malicious
   issuer/discovery URL cannot reach internal / cloud-metadata endpoints.
"""
from __future__ import annotations

import base64
import time
import uuid

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from sqlalchemy import select, text

from app.core.config import settings
from app.models.iam import SSOProviderConfig, SSOProviderType
from app.services import sso_service
from app.services.sso_service import (
    SSOAuthenticationError,
    discover_oidc_metadata,
    fetch_oidc_claims,
)


@pytest.fixture
def credential_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "DUCKDOCK_CREDENTIAL_KEY", key)
    return key


# ──────────────────────────────────────────────────────────────────────────
# RSA / JWKS helpers
# ──────────────────────────────────────────────────────────────────────────


def _rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = key.public_key().public_numbers()

    def _b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    kid = uuid.uuid4().hex
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64(nums.n),
        "e": _b64(nums.e),
    }
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem, jwk, kid


def _sign_id_token(pem: bytes, kid: str, claims: dict) -> str:
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


@pytest.mark.parametrize(
    "next_path",
    [
        None,
        "",
        "dashboard",
        "https://evil.example/phish",
        "//evil.example/phish",
        "/\\evil.example/phish",
        "/%5cevil.example/phish",
        "/%255cevil.example/phish",
        "/%2f%2fevil.example/phish",
        "/\x00evil",
        "/\nevil",
    ],
)
def test_sso_frontend_redirect_rejects_unsafe_paths(next_path):
    assert sso_service._frontend_redirect_target(next_path) == "/dashboard"


@pytest.mark.parametrize(
    "next_path",
    [
        "/dashboard",
        "/my-assets",
        "/namespaces/demo?tab=skills#latest",
    ],
)
def test_sso_frontend_redirect_accepts_internal_paths(next_path):
    assert sso_service._frontend_redirect_target(next_path) == next_path


# ──────────────────────────────────────────────────────────────────────────
# 1) Secret encryption at rest
# ──────────────────────────────────────────────────────────────────────────


async def test_client_secret_encrypted_at_rest(async_session, credential_key):
    provider = SSOProviderConfig(
        provider_type=SSOProviderType.OIDC,
        name="oidc-enc",
        client_id="cid",
        client_secret="super-secret-value",
    )
    async_session.add(provider)
    await async_session.flush()

    # Raw column value must be ciphertext, not the plaintext.
    raw = (
        await async_session.execute(
            text("SELECT client_secret FROM sso_provider_configs WHERE id = :i"),
            {"i": provider.id},
        )
    ).scalar_one()
    assert raw is not None
    assert "super-secret-value" not in raw
    # It is a Fernet token decryptable with the configured key.
    assert Fernet(credential_key.encode()).decrypt(raw.encode()).decode() == "super-secret-value"

    # Reading back through the ORM yields plaintext (fresh load, not identity map).
    async_session.expunge_all()
    refetched = (
        await async_session.execute(
            select(SSOProviderConfig).where(SSOProviderConfig.id == provider.id)
        )
    ).scalar_one()
    assert refetched.client_secret == "super-secret-value"


async def test_ldap_bind_password_encrypted_at_rest(async_session, credential_key):
    provider = SSOProviderConfig(
        provider_type=SSOProviderType.LDAP,
        name="ldap-enc",
        ldap_bind_password="bind-pw-123",
    )
    async_session.add(provider)
    await async_session.flush()

    raw = (
        await async_session.execute(
            text("SELECT ldap_bind_password FROM sso_provider_configs WHERE id = :i"),
            {"i": provider.id},
        )
    ).scalar_one()
    assert "bind-pw-123" not in raw
    async_session.expunge_all()
    refetched = await async_session.get(SSOProviderConfig, provider.id)
    assert refetched.ldap_bind_password == "bind-pw-123"


async def test_legacy_plaintext_secret_still_readable(async_session, credential_key):
    """A row whose column holds legacy plaintext (pre-encryption) reads back as-is."""
    provider = SSOProviderConfig(
        provider_type=SSOProviderType.OIDC,
        name="oidc-legacy",
        client_id="cid",
    )
    async_session.add(provider)
    await async_session.flush()

    # Simulate a legacy row by writing raw plaintext straight into the column.
    await async_session.execute(
        text("UPDATE sso_provider_configs SET client_secret = :v WHERE id = :i"),
        {"v": "legacy-plaintext-secret", "i": provider.id},
    )
    async_session.expunge_all()
    refetched = await async_session.get(SSOProviderConfig, provider.id)
    assert refetched.client_secret == "legacy-plaintext-secret"


async def test_none_secret_stays_none(async_session, credential_key):
    provider = SSOProviderConfig(
        provider_type=SSOProviderType.OIDC,
        name="oidc-none",
        client_id="cid",
    )
    async_session.add(provider)
    await async_session.flush()
    async_session.expunge_all()
    refetched = await async_session.get(SSOProviderConfig, provider.id)
    assert refetched.client_secret is None


# ──────────────────────────────────────────────────────────────────────────
# 2) OIDC id_token signature verification
# ──────────────────────────────────────────────────────────────────────────


def _provider() -> SSOProviderConfig:
    p = SSOProviderConfig(
        provider_type=SSOProviderType.OIDC,
        name="oidc-verify",
        issuer_url="https://idp.example.com",
        client_id="my-client-id",
    )
    p.id = 1
    return p


def _metadata() -> dict:
    return {
        "issuer": "https://idp.example.com",
        "jwks_uri": "https://idp.example.com/jwks",
        # no userinfo_endpoint → force id_token path
    }


async def test_valid_id_token_verifies(async_session, monkeypatch):
    pem, jwk, kid = _rsa_keypair()
    monkeypatch.setattr(
        sso_service, "_fetch_jwks", lambda *a, **k: _make_async({"keys": [jwk]})()
    )
    provider = _provider()
    claims = {
        "iss": "https://idp.example.com",
        "aud": "my-client-id",
        "sub": "user-123",
        "email": "u@example.com",
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
    }
    token = _sign_id_token(pem, kid, claims)
    out = await fetch_oidc_claims(provider, _metadata(), {"id_token": token})
    assert out["sub"] == "user-123"
    assert out["email"] == "u@example.com"


async def test_id_token_signed_by_wrong_key_rejected(async_session, monkeypatch):
    pem_signer, _, kid = _rsa_keypair()
    _, jwk_other, _ = _rsa_keypair()
    # JWKS exposes a DIFFERENT key (same kid to force a key match attempt).
    jwk_other = {**jwk_other, "kid": kid}
    monkeypatch.setattr(
        sso_service, "_fetch_jwks", lambda *a, **k: _make_async({"keys": [jwk_other]})()
    )
    provider = _provider()
    claims = {
        "iss": "https://idp.example.com",
        "aud": "my-client-id",
        "sub": "user-123",
        "exp": int(time.time()) + 600,
    }
    token = _sign_id_token(pem_signer, kid, claims)
    with pytest.raises(SSOAuthenticationError):
        await fetch_oidc_claims(provider, _metadata(), {"id_token": token})


async def test_id_token_with_unknown_kid_rejected(async_session, monkeypatch):
    pem, _, token_kid = _rsa_keypair()
    _, unrelated_jwk, _ = _rsa_keypair()
    monkeypatch.setattr(
        sso_service, "_fetch_jwks", lambda *a, **k: _make_async({"keys": [unrelated_jwk]})()
    )
    claims = {
        "iss": "https://idp.example.com",
        "aud": "my-client-id",
        "sub": "user-123",
        "exp": int(time.time()) + 600,
    }
    token = _sign_id_token(pem, token_kid, claims)
    with pytest.raises(SSOAuthenticationError):
        await fetch_oidc_claims(_provider(), _metadata(), {"id_token": token})


async def test_id_token_with_malformed_jwks_rejected(async_session, monkeypatch):
    pem, _, kid = _rsa_keypair()
    monkeypatch.setattr(sso_service, "_fetch_jwks", lambda *a, **k: _make_async({})())
    claims = {
        "iss": "https://idp.example.com",
        "aud": "my-client-id",
        "sub": "user-123",
        "exp": int(time.time()) + 600,
    }
    token = _sign_id_token(pem, kid, claims)
    with pytest.raises(SSOAuthenticationError):
        await fetch_oidc_claims(_provider(), _metadata(), {"id_token": token})


async def test_expired_id_token_rejected(async_session, monkeypatch):
    pem, jwk, kid = _rsa_keypair()
    monkeypatch.setattr(
        sso_service, "_fetch_jwks", lambda *a, **k: _make_async({"keys": [jwk]})()
    )
    provider = _provider()
    claims = {
        "iss": "https://idp.example.com",
        "aud": "my-client-id",
        "sub": "user-123",
        "exp": int(time.time()) - 60,  # expired
        "iat": int(time.time()) - 600,
    }
    token = _sign_id_token(pem, kid, claims)
    with pytest.raises(SSOAuthenticationError):
        await fetch_oidc_claims(provider, _metadata(), {"id_token": token})


async def test_wrong_audience_id_token_rejected(async_session, monkeypatch):
    pem, jwk, kid = _rsa_keypair()
    monkeypatch.setattr(
        sso_service, "_fetch_jwks", lambda *a, **k: _make_async({"keys": [jwk]})()
    )
    provider = _provider()
    claims = {
        "iss": "https://idp.example.com",
        "aud": "some-other-client",  # wrong aud
        "sub": "user-123",
        "exp": int(time.time()) + 600,
    }
    token = _sign_id_token(pem, kid, claims)
    with pytest.raises(SSOAuthenticationError):
        await fetch_oidc_claims(provider, _metadata(), {"id_token": token})


async def test_no_unverified_fallback_when_jwks_unavailable(async_session, monkeypatch):
    """Even a structurally-valid token must NOT be accepted without verification."""
    pem, _, kid = _rsa_keypair()

    async def _boom(*a, **k):
        raise RuntimeError("jwks fetch failed")

    monkeypatch.setattr(sso_service, "_fetch_jwks", _boom)
    provider = _provider()
    claims = {
        "iss": "https://idp.example.com",
        "aud": "my-client-id",
        "sub": "user-123",
        "exp": int(time.time()) + 600,
    }
    token = _sign_id_token(pem, kid, claims)
    with pytest.raises((SSOAuthenticationError, RuntimeError)):
        await fetch_oidc_claims(provider, _metadata(), {"id_token": token})


# ──────────────────────────────────────────────────────────────────────────
# 3) SSRF guard on SSO outbound
# ──────────────────────────────────────────────────────────────────────────


async def test_discovery_url_metadata_ip_rejected(async_session, monkeypatch):
    """A discovery URL pointing at the cloud metadata IP is rejected pre-flight."""
    import httpx

    async def _no_http(*a, **k):  # pragma: no cover - must never be hit
        raise AssertionError("HTTP request must not be made for a blocked URL")

    # If the guard is ever bypassed, an actual HTTP attempt would fail loudly.
    monkeypatch.setattr(httpx.AsyncClient, "get", _no_http, raising=False)

    provider = SSOProviderConfig(
        provider_type=SSOProviderType.OIDC,
        name="ssrf-meta",
        issuer_url="http://169.254.169.254",
    )
    provider.extra_config = {
        "discovery_url": "http://169.254.169.254/.well-known/openid-configuration"
    }
    with pytest.raises(ValueError):
        await discover_oidc_metadata(provider)


async def test_discovery_issuer_loopback_rejected(async_session):
    provider = SSOProviderConfig(
        provider_type=SSOProviderType.OIDC,
        name="ssrf-loop",
        issuer_url="http://127.0.0.1",
    )
    with pytest.raises(ValueError):
        await discover_oidc_metadata(provider)


async def test_jwks_private_ip_rejected(async_session, monkeypatch):
    pem, _, kid = _rsa_keypair()
    provider = _provider()
    metadata = {
        "issuer": "https://idp.example.com",
        "jwks_uri": "http://10.0.0.5/jwks",  # private
    }
    claims = {
        "iss": "https://idp.example.com",
        "aud": "my-client-id",
        "sub": "user-123",
        "exp": int(time.time()) + 600,
    }
    token = _sign_id_token(pem, kid, claims)
    with pytest.raises(ValueError):
        await fetch_oidc_claims(provider, metadata, {"id_token": token})


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


def _make_async(value):
    async def _inner(*a, **k):
        return value

    return _inner
