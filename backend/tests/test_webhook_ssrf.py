"""Webhook target URL SSRF hardening (L0-SEC-WEBHOOK-SSRF).

Tenant-controlled webhook URLs must not be allowed to reach internal /
loopback / link-local / cloud-metadata endpoints. Two layers:

  1. CREATE/UPDATE schema validation (``WebhookCreate`` / ``WebhookUpdate``)
     rejects non-http(s) schemes and private/loopback/link-local/reserved IP
     literals (incl. 169.254.169.254) at write time → pydantic ValidationError
     (HTTP 422 at the API boundary).
  2. A pre-delivery guard that *resolves DNS* and rejects any host that resolves
     to a private IP — defeating DNS rebinding (a public name that points at an
     internal address by the time delivery runs).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.webhook import WebhookCreate, WebhookUpdate, validate_external_url
from app.workers.webhook_tasks import WebhookSSRFError, assert_safe_delivery_url


# ── schema-level (CREATE / UPDATE) ──────────────────────────────────

_BLOCKED_URLS = [
    "http://127.0.0.1",
    "http://127.0.0.1/hook",
    "http://169.254.169.254/latest/meta-data",  # cloud metadata
    "http://10.0.0.5",
    "http://172.16.0.1/x",
    "http://192.168.1.10",
    "http://0.0.0.0",
    "http://localhost:8000/hook",
    "http://[::1]/hook",  # IPv6 loopback
    "file:///etc/passwd",
    "gopher://127.0.0.1/x",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "//evil.example.com",  # scheme-relative → no scheme
    "not a url",
]


@pytest.mark.parametrize("url", _BLOCKED_URLS)
def test_create_rejects_unsafe_url(url):
    with pytest.raises(ValidationError):
        WebhookCreate(name="h", url=url, events=["scan.completed"])


@pytest.mark.parametrize("url", _BLOCKED_URLS)
def test_update_rejects_unsafe_url(url):
    with pytest.raises(ValidationError):
        WebhookUpdate(url=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/incoming",
        "http://example.com/webhook",
        "https://api.example.org:8443/events",
    ],
)
def test_create_accepts_public_url(url):
    hook = WebhookCreate(name="h", url=url, events=["scan.completed"])
    assert hook.url == url


def test_update_url_optional_none_is_allowed():
    # PATCH with no url change must not be forced through the validator.
    body = WebhookUpdate(name="renamed")
    assert body.url is None


# ── validate_external_url unit coverage ─────────────────────────────


@pytest.mark.parametrize("url", _BLOCKED_URLS)
def test_validate_external_url_rejects(url):
    with pytest.raises(ValueError):
        validate_external_url(url)


def test_validate_external_url_returns_value_for_public():
    assert validate_external_url("https://example.com/x") == "https://example.com/x"


# ── pre-delivery guard (DNS resolution, anti-rebinding) ─────────────


def test_pre_delivery_guard_rejects_literal_private():
    with pytest.raises(WebhookSSRFError):
        assert_safe_delivery_url("http://10.0.0.5/hook")


def test_pre_delivery_guard_rejects_metadata_ip():
    with pytest.raises(WebhookSSRFError):
        assert_safe_delivery_url("http://169.254.169.254/latest/meta-data")


def test_pre_delivery_guard_rejects_non_http_scheme():
    with pytest.raises(WebhookSSRFError):
        assert_safe_delivery_url("file:///etc/passwd")


def test_pre_delivery_guard_rejects_dns_rebinding(monkeypatch):
    """A public-looking host that *resolves* to a private IP is rejected."""
    import app.workers.webhook_tasks as wt

    def _fake_getaddrinfo(host, *args, **kwargs):
        # Pretend evil.example.com resolves to a private RFC1918 address.
        return [(2, 1, 6, "", ("10.1.2.3", 0))]

    monkeypatch.setattr(wt.socket, "getaddrinfo", _fake_getaddrinfo)
    with pytest.raises(WebhookSSRFError):
        assert_safe_delivery_url("https://evil.example.com/hook")


def test_pre_delivery_guard_allows_public_resolution(monkeypatch):
    import app.workers.webhook_tasks as wt

    def _fake_getaddrinfo(host, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]  # example.com public IP

    monkeypatch.setattr(wt.socket, "getaddrinfo", _fake_getaddrinfo)
    # Should not raise.
    assert_safe_delivery_url("https://hooks.example.com/incoming")
