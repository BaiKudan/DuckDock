"""SSRF-safe outbound HTTP: resolve, validate every address, and PIN the connection.

The literal guards (``validate_external_url`` / ``validate_llm_base_url``) only reject
IP *literals*; a public hostname that resolves to an internal/metadata IP slips past
them, and resolving "then" connecting leaves a DNS-rebinding window (the connection
re-resolves and can land on a different, internal IP).

:func:`ssrf_safe_request` closes both holes:
  1. literal + scheme guard (reuse ``validate_external_url``);
  2. resolve the host once and reject if **any** returned address is private/loopback/
     link-local/reserved/metadata;
  3. **pin** the connection to a validated IP — the request is sent to that exact IP
     while the original hostname is preserved for the ``Host`` header and the TLS SNI /
     certificate check (httpcore ``sni_hostname`` extension), so there is no second
     resolution to rebind.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.schemas.webhook import is_blocked_ip, validate_external_url


class SSRFError(ValueError):
    """An outbound URL is not safe to fetch (blocked scheme / host / resolved address).

    Subclasses ``ValueError`` so it satisfies callers (and tests) that treat a rejected
    URL as a value error, while still being catchable specifically (e.g. webhook delivery
    marks it a permanent failure, not a retryable transport error).
    """


def _validated_addresses(host: str, port: int | None) -> list[str]:
    """Resolve *host* and return its addresses, raising if any is blocked.

    An IP literal is validated directly; a hostname is resolved via getaddrinfo and
    EVERY returned address must be public.
    """
    bare = host.strip("[]")
    try:
        ipaddress.ip_address(bare)
        addresses = [bare]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise SSRFError(f"host {host!r} did not resolve: {exc}") from exc
        addresses = [str(info[4][0]) for info in infos if info[4]]
    if not addresses:
        raise SSRFError(f"host {host!r} did not resolve to any address")
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if is_blocked_ip(ip):
            raise SSRFError(
                f"host {host!r} resolves to blocked address {addr} "
                "(private/loopback/link-local/reserved)"
            )
    return addresses


async def ssrf_safe_request(
    method: str,
    url: str,
    *,
    timeout: float = 15.0,
    follow_redirects: bool = False,
    client: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Send an outbound HTTP request with SSRF resolve+validate+pin.

    Raises :class:`SSRFError` for a blocked scheme/host/address. The connection is
    pinned to a validated IP (no rebinding), with the original host kept for ``Host``
    and TLS SNI so HTTPS certificate verification still targets the real hostname.
    """
    try:
        validate_external_url(url)
    except ValueError as exc:
        raise SSRFError(str(exc)) from exc

    parts = urlsplit(url)
    host = parts.hostname or ""
    addresses = await asyncio.to_thread(_validated_addresses, host, parts.port)
    pinned = addresses[0]

    ip_netloc = f"[{pinned}]" if ":" in pinned else pinned
    if parts.port:
        ip_netloc += f":{parts.port}"
    ip_url = urlunsplit((parts.scheme, ip_netloc, parts.path or "/", parts.query, ""))

    host_header = f"[{host}]" if ":" in host else host
    if parts.port:
        host_header += f":{parts.port}"
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("Host", host_header)

    if client is None:
        active = httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects)
        own_client = True
    else:
        active = client
        own_client = False
    try:
        request = active.build_request(method, ip_url, headers=headers, **kwargs)
        # Pin TLS SNI + cert verification to the real hostname while connecting to the IP.
        request.extensions = {**request.extensions, "sni_hostname": host}
        return await active.send(request)
    finally:
        if own_client:
            await active.aclose()
