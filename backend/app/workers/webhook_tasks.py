"""Celery tasks for webhook delivery."""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from urllib.parse import urlsplit

from app.schemas.webhook import _ALLOWED_SCHEMES, is_blocked_ip
from app.services.ssrf import SSRFError, ssrf_safe_request
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session


class WebhookSSRFError(ValueError):
    """A webhook target URL failed the pre-delivery SSRF guard."""


def assert_safe_delivery_url(url: str) -> None:
    """Re-validate a webhook URL right before delivery, resolving DNS.

    The write-time guard (``app/schemas/webhook.py``) rejects private IP
    *literals*, but a stored public hostname can still resolve to an internal
    address by delivery time (DNS rebinding / TOCTOU). Here we require an
    http/https scheme and reject if *any* resolved address is private/loopback/
    link-local/reserved (incl. the cloud metadata IP 169.254.169.254).

    Raises :class:`WebhookSSRFError` if the URL must not be fetched.
    """
    value = (url or "").strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise WebhookSSRFError(
            f"webhook url must use http or https scheme, got {parts.scheme!r}"
        )
    host = parts.hostname
    if not host:
        raise WebhookSSRFError(f"webhook url has no host: {value!r}")

    # An IP literal needs no DNS lookup; a hostname is resolved to all of its
    # addresses and every one of them must be public.
    try:
        addresses = [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise WebhookSSRFError(f"webhook host {host!r} did not resolve: {exc}")
        addresses = []
        for info in infos:
            sockaddr = info[4]
            try:
                addresses.append(ipaddress.ip_address(sockaddr[0]))
            except ValueError:
                continue

    if not addresses:
        raise WebhookSSRFError(f"webhook host {host!r} did not resolve to any IP")
    for ip in addresses:
        if is_blocked_ip(ip):
            raise WebhookSSRFError(
                f"webhook host {host!r} resolves to blocked address {ip} "
                "(private/loopback/link-local/reserved)"
            )


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def deliver_webhook(self, webhook_id: int, event: str, payload: dict):
    asyncio.run(_async_deliver(self, webhook_id, event, payload))


async def _async_deliver(task, webhook_id: int, event: str, payload: dict):
    from sqlalchemy import select
    from app.models.webhook import Webhook, WebhookDelivery
    from app.models.audit import AuditLog

    async with worker_db_session(echo=False) as db:
        r = await db.execute(select(Webhook).where(Webhook.id == webhook_id))
        hook = r.scalar_one_or_none()
        if not hook or not hook.is_active:
            return

        body = json.dumps({"event": event, "data": payload}).encode()
        headers = {
            "Content-Type": "application/json",
            "X-DuckDock-Event": event,
        }
        if hook.secret:
            sig = hmac.new(hook.secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-DuckDock-Signature"] = f"sha256={sig}"

        response_status = None
        response_body = None
        success = False
        try:
            # SSRF-safe delivery: validate + resolve + PIN the connection to a
            # validated IP, with the hostname kept for Host/TLS SNI — so there is no
            # second DNS resolution to rebind onto an internal address. A guard
            # failure is permanent (no retry); transport errors retry.
            resp = await ssrf_safe_request(
                "POST", str(hook.url), content=body, headers=headers, timeout=10
            )
            response_status = resp.status_code
            response_body = resp.text[:4000]
            success = 200 <= resp.status_code < 300
        except SSRFError as exc:
            response_body = f"blocked by SSRF guard: {exc}"[:4000]
        except Exception as exc:
            response_body = str(exc)[:4000]
            if task.request.retries < task.max_retries:
                raise task.retry(exc=exc)

        delivery = WebhookDelivery(
            webhook_id=webhook_id,
            event=event,
            payload=payload,
            response_status=response_status,
            response_body=response_body,
            success=success,
        )
        db.add(delivery)
        db.add(
            AuditLog(
                action="webhook.delivery.completed",
                resource_type="webhook",
                resource_id=hook.id,
                namespace_id=hook.namespace_id,
                details={"event": event, "success": success, "status": response_status},
            )
        )
        await db.commit()
