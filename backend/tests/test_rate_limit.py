"""SEC-03 / L0-SEC-RL-FAIL / L0-SEC-XFF: true failure-lockout rate limiter.

Covers the safety contract: a no-op while disabled (default) so existing dev/tests are
unaffected, and a 429 + lockout once the *failure* threshold is exceeded when enabled — all
against an in-memory fake backend so no real Redis is required. Also covers the precheck /
register_failure split (successes never consume the budget) and the X-Forwarded-For trust model
(spoofed XFF from an untrusted peer cannot move another client's bucket).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.core.ratelimit import InMemoryRateLimitBackend, RateLimiter


def _limiter(
    *,
    enabled: bool,
    max_attempts: int = 3,
    trusted_proxies: list[str] | None = None,
) -> RateLimiter:
    settings = Settings(
        RATE_LIMIT_ENABLED=enabled,
        RATE_LIMIT_MAX_ATTEMPTS=max_attempts,
        RATE_LIMIT_WINDOW_SECONDS=60,
        RATE_LIMIT_LOCKOUT_SECONDS=300,
        RATE_LIMIT_TRUSTED_PROXIES=trusted_proxies or [],
    )
    return RateLimiter(InMemoryRateLimitBackend(), settings)


class _FakeRequest:
    """Minimal stand-in for starlette.Request to drive RateLimiter.client_ip."""

    class _Client:
        def __init__(self, host: str) -> None:
            self.host = host

    def __init__(self, *, peer: str | None, xff: str | None = None) -> None:
        self.client = self._Client(peer) if peer is not None else None
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}


async def _fail_n(limiter: RateLimiter, scope: str, identity: str, ip: str, n: int) -> None:
    """Register ``n`` failures, swallowing the 429 the threshold-tripping one raises."""
    for _ in range(n):
        try:
            await limiter.register_failure(scope, identity, ip)
        except HTTPException:
            pass


async def test_disabled_limiter_is_a_noop_even_past_threshold():
    limiter = _limiter(enabled=False, max_attempts=1)
    # Far past the would-be threshold; must never raise while disabled.
    for _ in range(20):
        await limiter.precheck("login", "alice", "1.2.3.4")
        await limiter.register_failure("login", "alice", "1.2.3.4")
    assert limiter.enabled is False


async def test_default_settings_keep_limiter_disabled():
    # CRITICAL SAFETY: the shipped default must be OFF.
    assert Settings().RATE_LIMIT_ENABLED is False


async def test_default_trusted_proxies_is_empty():
    # L0-SEC-XFF: with no trusted proxies configured, XFF must never be honoured.
    assert Settings().RATE_LIMIT_TRUSTED_PROXIES == []


async def test_enabled_limiter_429s_after_failure_threshold_then_locks_out():
    limiter = _limiter(enabled=True, max_attempts=3)

    # First N failures are recorded without locking out.
    for _ in range(3):
        await limiter.precheck("login", "alice", "1.2.3.4")
        await limiter.register_failure("login", "alice", "1.2.3.4")

    # The (N+1)th failure trips the limit -> 429.
    await limiter.precheck("login", "alice", "1.2.3.4")
    with pytest.raises(HTTPException) as exc_info:
        await limiter.register_failure("login", "alice", "1.2.3.4")
    assert exc_info.value.status_code == 429

    # Once locked out, even a no-increment precheck is rejected with 429.
    with pytest.raises(HTTPException) as exc_info2:
        await limiter.precheck("login", "alice", "1.2.3.4")
    assert exc_info2.value.status_code == 429


async def test_limit_is_scoped_per_ip_username_pair():
    limiter = _limiter(enabled=True, max_attempts=2)

    # Exhaust alice@ip-A (2 failures allowed, 3rd trips).
    await _fail_n(limiter, "login", "alice", "10.0.0.1", 3)
    with pytest.raises(HTTPException):
        await limiter.precheck("login", "alice", "10.0.0.1")

    # A different username (same ip) and a different ip (same username) are independent.
    await limiter.precheck("login", "bob", "10.0.0.1")
    await limiter.precheck("login", "alice", "10.0.0.2")


async def test_scope_separates_login_from_register():
    limiter = _limiter(enabled=True, max_attempts=1)

    await _fail_n(limiter, "login", "alice", "10.0.0.1", 2)
    with pytest.raises(HTTPException):
        await limiter.precheck("login", "alice", "10.0.0.1")

    # A different scope (register) for the same identity has its own budget.
    await limiter.precheck("register", "alice", "10.0.0.1")


async def test_username_is_case_insensitive_in_key():
    limiter = _limiter(enabled=True, max_attempts=1)

    await _fail_n(limiter, "login", "Alice", "10.0.0.1", 2)
    # Same identity in a different case must share the bucket.
    with pytest.raises(HTTPException):
        await limiter.precheck("login", "alice", "10.0.0.1")


# --- L0-SEC-RL-FAIL: successes must not consume the failure budget --------------------------


async def test_repeated_successful_logins_never_lock_out():
    """N successful logins in a row (precheck only, no register_failure) never lock."""
    limiter = _limiter(enabled=True, max_attempts=3)
    # A successful login = precheck passes, NOTHING is registered.
    for _ in range(50):
        await limiter.precheck("login", "alice", "1.2.3.4")
    # The bucket is still completely clean.
    await limiter.precheck("login", "alice", "1.2.3.4")


async def test_failures_lock_out_and_correct_password_still_429s():
    """N failed attempts -> next precheck is 429 and stays locked even with a correct password."""
    limiter = _limiter(enabled=True, max_attempts=3)

    # Exhaust the failure budget (3 allowed, 4th trips the lockout).
    await _fail_n(limiter, "login", "alice", "1.2.3.4", 4)

    # A subsequent attempt — even one that would succeed — is blocked at precheck.
    with pytest.raises(HTTPException) as exc_info:
        await limiter.precheck("login", "alice", "1.2.3.4")
    assert exc_info.value.status_code == 429

    # And it stays locked on the next try too.
    with pytest.raises(HTTPException):
        await limiter.precheck("login", "alice", "1.2.3.4")


# --- L0-SEC-XFF: X-Forwarded-For trust model -----------------------------------------------


def test_client_ip_ignores_xff_from_untrusted_peer():
    """Default (no trusted proxies): XFF is ignored, the peer IP is the key."""
    limiter = _limiter(enabled=True)
    req = _FakeRequest(peer="203.0.113.9", xff="1.1.1.1, 2.2.2.2")
    assert limiter.client_ip(req) == "203.0.113.9"


def test_client_ip_ignores_xff_even_with_some_trusted_proxies_if_peer_untrusted():
    """A spoofed XFF from a peer that is NOT a trusted proxy does not change the bucket."""
    limiter = _limiter(enabled=True, trusted_proxies=["10.0.0.0/8"])
    # Peer is a public address, not within the trusted CIDR -> XFF ignored.
    req = _FakeRequest(peer="203.0.113.9", xff="9.9.9.9")
    assert limiter.client_ip(req) == "203.0.113.9"


def test_spoofed_xff_does_not_change_bucket():
    """Two requests from the same untrusted peer share a bucket regardless of spoofed XFF."""
    limiter = _limiter(enabled=True)
    real = _FakeRequest(peer="203.0.113.9")
    spoofed = _FakeRequest(peer="203.0.113.9", xff="8.8.8.8")
    assert limiter.client_ip(real) == limiter.client_ip(spoofed) == "203.0.113.9"


def test_client_ip_honours_xff_behind_trusted_proxy():
    """With a trusted proxy configured, the real client XFF (rightmost hop) is honoured."""
    limiter = _limiter(enabled=True, trusted_proxies=["10.0.0.1"])
    # Peer is the trusted proxy; the rightmost XFF entry is what the proxy actually saw.
    req = _FakeRequest(peer="10.0.0.1", xff="198.51.100.7")
    assert limiter.client_ip(req) == "198.51.100.7"


def test_client_ip_honours_xff_behind_trusted_cidr():
    """Trusted proxy specified as a CIDR also works."""
    limiter = _limiter(enabled=True, trusted_proxies=["10.0.0.0/8"])
    req = _FakeRequest(peer="10.1.2.3", xff="198.51.100.7, 10.0.0.1")
    # Rightmost hop is the trusted proxy's own injection; take the rightmost entry.
    assert limiter.client_ip(req) == "10.0.0.1"


def test_client_ip_falls_back_to_unknown_without_peer():
    limiter = _limiter(enabled=True, trusted_proxies=["10.0.0.0/8"])
    req = _FakeRequest(peer=None, xff="1.2.3.4")
    assert limiter.client_ip(req) == "unknown"


def test_login_endpoint_returns_429_when_limiter_enabled():
    """End-to-end: the /login route enforces the limiter via its FastAPI dependency."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from starlette.testclient import TestClient

    import app.models  # noqa: F401 - register models for create_all
    from app.core.database import Base, get_db
    from app.core.ratelimit import get_rate_limiter
    from app.main import app as fastapi_app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            yield session

    enabled_limiter = _limiter(enabled=True, max_attempts=2)

    fastapi_app.dependency_overrides[get_db] = _override_db
    fastapi_app.dependency_overrides[get_rate_limiter] = lambda: enabled_limiter
    try:
        with TestClient(fastapi_app) as client:
            payload = {"username": "ghost", "password": "nope"}
            # Within budget: limiter passes, login fails on unknown user (401).
            assert client.post("/api/v1/auth/login", json=payload).status_code == 401
            assert client.post("/api/v1/auth/login", json=payload).status_code == 401
            # Threshold exceeded: limiter trips before the DB lookup -> 429.
            assert client.post("/api/v1/auth/login", json=payload).status_code == 429
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
        fastapi_app.dependency_overrides.pop(get_rate_limiter, None)
