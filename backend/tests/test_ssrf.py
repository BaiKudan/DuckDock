"""SSRF-safe outbound HTTP: resolve + validate-all + PIN (closes the DNS-rebinding hole).

The literal guard alone lets a public hostname resolving to an internal/metadata IP
through. ``ssrf_safe_request`` resolves the host, rejects if ANY address is blocked,
and connects to the validated IP while keeping the hostname for Host + TLS SNI — so
there is no second resolution to rebind onto an internal address (covers the SSO and
webhook SSRF findings).
"""
from __future__ import annotations

import httpx
import pytest

import app.services.ssrf as ssrf_mod
from app.services.ssrf import SSRFError, ssrf_safe_request

pytestmark = pytest.mark.asyncio


def _fake_getaddrinfo(ip: str):
    def _resolver(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (ip, port or 0))]

    return _resolver


async def test_blocks_host_resolving_to_private(monkeypatch):
    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", _fake_getaddrinfo("10.1.2.3"))
    with pytest.raises(SSRFError):
        await ssrf_safe_request("GET", "https://evil.example.com/x")


async def test_blocks_host_resolving_to_cloud_metadata(monkeypatch):
    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    with pytest.raises(SSRFError):
        await ssrf_safe_request("GET", "https://evil.example.com/latest/meta-data")


async def test_blocks_private_literal_and_bad_scheme():
    with pytest.raises(SSRFError):
        await ssrf_safe_request("GET", "http://127.0.0.1/x")
    with pytest.raises(SSRFError):
        await ssrf_safe_request("GET", "file:///etc/passwd")


async def test_pins_connection_to_validated_ip(monkeypatch):
    """A public name is connected via its validated IP, with host kept for Host + TLS SNI."""
    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["host"] = request.headers.get("host")
        captured["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        resp = await ssrf_safe_request(
            "GET", "https://hooks.example.com/incoming", client=client
        )
    finally:
        await client.aclose()

    assert resp.status_code == 200
    # Connection target is the validated IP — not a hostname re-resolved at connect time.
    assert str(captured["url"]).startswith("https://93.184.216.34/")
    # Original hostname preserved for routing + certificate verification.
    assert captured["host"] == "hooks.example.com"
    assert captured["sni"] == "hooks.example.com"
