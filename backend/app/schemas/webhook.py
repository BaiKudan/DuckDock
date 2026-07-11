"""Webhook schemas + SSRF hardening for tenant-supplied target URLs.

Webhook target URLs are tenant-controlled, so an unvalidated URL is an SSRF
vector: a tenant could point a webhook at ``127.0.0.1``, an internal service,
or the cloud metadata endpoint ``169.254.169.254`` and have the server fetch it.

:func:`validate_external_url` is the write-time guard (CREATE/UPDATE): it
requires an explicit http/https scheme and rejects private/loopback/link-local/
reserved IP **literals** plus well-known local hostnames. It does **not** resolve
DNS (no network call on write); the pre-delivery guard in
``app/workers/webhook_tasks.py`` re-validates *with* DNS resolution to defeat
DNS rebinding.
"""
import ipaddress
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

_ALLOWED_SCHEMES = {"http", "https"}
# Hostnames that must be treated as local without resolving DNS.
_LOCAL_HOSTNAMES = {"localhost", "ip6-localhost", "ip6-loopback"}


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if *ip* is in a range a webhook must never reach.

    Covers loopback (127/8, ::1), RFC1918 private (10/8, 172.16/12, 192.168/16),
    link-local incl. cloud metadata 169.254.169.254 (169.254/16, fe80::/10),
    reserved, and unspecified (0.0.0.0 / ::) ranges.
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


def _is_blocked_host_literal(host: str) -> bool:
    """True if *host* is a blocked IP literal or a known local hostname.

    Public hostnames return False (no DNS resolution here)."""
    bare = host.strip("[]")  # IPv6 literals may carry brackets
    if bare.lower() in _LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        return False  # not an IP literal → treat as (public) hostname
    return is_blocked_ip(ip)


def validate_external_url(url: str) -> str:
    """Validate a tenant-supplied webhook URL (write-time, no DNS).

    Requires an explicit http/https scheme with a host, and rejects
    private/loopback/link-local/reserved IP literals and local hostnames.
    Raises :class:`ValueError` (→ HTTP 422 via pydantic) on failure; returns the
    original string on success.
    """
    value = (url or "").strip()
    if not value:
        raise ValueError("webhook url is empty")

    parts = urlsplit(value)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"webhook url must use http or https scheme, got {parts.scheme!r}"
        )
    if not parts.hostname:
        raise ValueError(f"webhook url has no host: {value!r}")
    if _is_blocked_host_literal(parts.hostname):
        raise ValueError(
            f"webhook url host {parts.hostname!r} resolves to a private/loopback/"
            "link-local/reserved address and is not allowed"
        )
    return url


class WebhookCreate(BaseModel):
    name: str
    url: str
    secret: str | None = None
    events: list[str] = Field(default_factory=list)
    is_active: bool = True

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return validate_external_url(v)


class WebhookUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return validate_external_url(v)


class WebhookOut(BaseModel):
    id: int
    namespace_id: int
    name: str
    url: str
    events: list[str]
    is_active: bool
    created_by: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class WebhookDeliveryOut(BaseModel):
    id: int
    webhook_id: int
    event: str
    response_status: int | None
    response_body: str | None
    payload: dict | None
    success: bool
    attempted_at: datetime

    model_config = {"from_attributes": True}
