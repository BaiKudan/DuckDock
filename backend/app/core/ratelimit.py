"""SEC-03: Redis-backed failure-lockout rate limiter for auth/token/register paths.

Design goals:
- **No-op unless ``settings.RATE_LIMIT_ENABLED`` is True.** The default is False, so dev /
  test runtimes (and ``from app.main import app``) are completely unaffected and never need a
  real Redis. The limiter dependency short-circuits before touching any backend.
- **Injectable backend** via the :class:`RateLimitBackend` protocol. Production uses
  :class:`RedisRateLimitBackend` over ``redis.asyncio`` (already a dependency via celery).

The limiter is a **true failure-lockout** keyed by ``ip + username``: callers run
:meth:`RateLimiter.precheck` *before* attempting auth (it only raises HTTP 429 while a lockout is
active and never increments), and call :meth:`RateLimiter.register_failure` *after* a verified
auth failure (it increments the counter; once it exceeds ``RATE_LIMIT_MAX_ATTEMPTS`` within
``RATE_LIMIT_WINDOW_SECONDS`` it sets a lockout for ``RATE_LIMIT_LOCKOUT_SECONDS`` and raises 429).
Successful logins therefore never consume the budget, so a legitimate user is never locked out by
their own success.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Protocol, runtime_checkable

from fastapi import HTTPException, Request, status

from app.core.config import Settings, settings as global_settings

_KEY_PREFIX = "duckdock:ratelimit"


@runtime_checkable
class RateLimitBackend(Protocol):
    """Minimal async key/value backend the limiter needs.

    The production implementation is :class:`RedisRateLimitBackend`.
    """

    async def incr_with_ttl(self, key: str, window_seconds: int) -> int:
        """Increment ``key`` and ensure it expires in ``window_seconds``; return new value."""
        ...

    async def set_lockout(self, key: str, lockout_seconds: int) -> None:
        """Mark ``key`` as locked out for ``lockout_seconds``."""
        ...

    async def is_locked(self, key: str) -> bool:
        """Return True while a lockout flag for ``key`` is still active."""
        ...


class RedisRateLimitBackend:
    """Production backend over ``redis.asyncio`` (lazy client; reuses ``settings.REDIS_URL``)."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis_asyncio

            self._client = redis_asyncio.from_url(self._redis_url, decode_responses=True)
        return self._client

    async def incr_with_ttl(self, key: str, window_seconds: int) -> int:
        client = self._get_client()
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            results = await pipe.execute()
        return int(results[0])

    async def set_lockout(self, key: str, lockout_seconds: int) -> None:
        client = self._get_client()
        await client.set(key, "1", ex=lockout_seconds)

    async def is_locked(self, key: str) -> bool:
        client = self._get_client()
        return bool(await client.exists(key))


class RateLimiter:
    """Failure-lockout rate limiter. A no-op while disabled in settings."""

    def __init__(self, backend: RateLimitBackend, settings: Settings) -> None:
        self._backend = backend
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self._settings.RATE_LIMIT_ENABLED)

    def client_ip(self, request: Request) -> str:
        """Derive the client IP, honouring X-Forwarded-For *only* behind a trusted proxy.

        L0-SEC-XFF: an X-Forwarded-For header is attacker-controlled unless the immediate peer
        (``request.client.host``) is itself a trusted reverse proxy. So we trust XFF only when the
        peer is listed in ``RATE_LIMIT_TRUSTED_PROXIES`` (IPs or CIDRs); then we take the *last*
        (rightmost) XFF entry — the hop the trusted proxy actually saw. Otherwise we ignore XFF
        entirely and key on the peer IP, so a spoofed header cannot move another client's bucket.
        With the default empty trusted-proxy list, XFF is never honoured.
        """
        peer = request.client.host if request.client is not None else None
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded and peer is not None and self._is_trusted_proxy(peer):
            hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            if hops:
                return hops[-1]
        if peer is not None:
            return peer
        return "unknown"

    def _is_trusted_proxy(self, peer: str) -> bool:
        """True if ``peer`` falls within any configured trusted-proxy IP/CIDR."""
        try:
            peer_addr = ipaddress.ip_address(peer)
        except ValueError:
            return False
        for entry in self._settings.RATE_LIMIT_TRUSTED_PROXIES:
            entry = entry.strip()
            if not entry:
                continue
            try:
                if "/" in entry:
                    if peer_addr in ipaddress.ip_network(entry, strict=False):
                        return True
                elif peer_addr == ipaddress.ip_address(entry):
                    return True
            except ValueError:
                continue
        return False

    def _keys(self, scope: str, identity: str, ip: str) -> tuple[str, str]:
        """Return ``(counter_key, lockout_key)`` for ``scope`` keyed by ``ip + identity``."""
        identity = (identity or "").strip().lower()
        base = f"{_KEY_PREFIX}:{scope}:{ip}:{identity}"
        return f"{base}:count", f"{base}:lock"

    async def precheck(self, scope: str, identity: str, ip: str) -> None:
        """Reject locked-out keys *before* an auth attempt. Never increments the counter.

        No-op unless enabled. Raises HTTP 429 only while the derived key is currently locked out;
        a successful auth that follows therefore never consumes the failure budget.
        """
        if not self.enabled:
            return
        _counter_key, lockout_key = self._keys(scope, identity, ip)
        if await self._backend.is_locked(lockout_key):
            raise self._too_many()

    async def register_failure(self, scope: str, identity: str, ip: str) -> None:
        """Record one auth *failure* for the key; lock out + raise 429 once over the threshold.

        No-op unless enabled. Call this only after a verified auth failure (unknown user / bad
        password). Increments the windowed counter; when it exceeds ``RATE_LIMIT_MAX_ATTEMPTS`` it
        sets a lockout for ``RATE_LIMIT_LOCKOUT_SECONDS`` and raises HTTP 429.
        """
        if not self.enabled:
            return
        counter_key, lockout_key = self._keys(scope, identity, ip)
        attempts = await self._backend.incr_with_ttl(
            counter_key, self._settings.RATE_LIMIT_WINDOW_SECONDS
        )
        if attempts > self._settings.RATE_LIMIT_MAX_ATTEMPTS:
            await self._backend.set_lockout(
                lockout_key, self._settings.RATE_LIMIT_LOCKOUT_SECONDS
            )
            raise self._too_many()

    @staticmethod
    def _too_many() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please try again later.",
            headers={"Retry-After": "60"},
        )


def _build_default_limiter() -> RateLimiter:
    """Construct the process-wide limiter.

    The backend object is created eagerly but stays inert: when the limiter is disabled it is
    never touched, and the Redis client itself is created lazily on first real use. So importing
    this module (and ``app.main``) never opens a connection.
    """
    backend: RateLimitBackend = RedisRateLimitBackend(global_settings.REDIS_URL)
    return RateLimiter(backend, global_settings)


# Process-wide singleton used by the FastAPI dependency.
limiter = _build_default_limiter()


def get_rate_limiter() -> RateLimiter:
    """FastAPI dependency returning the active limiter (overridable in tests)."""
    return limiter
