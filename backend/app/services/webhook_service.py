"""Webhook dispatch helper — enqueues delivery tasks."""
import hashlib
import hmac




def _sign_payload(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def dispatch_event(
    db,
    namespace_id: int,
    event: str,
    payload: dict,
) -> None:
    """
    Find all active webhooks for namespace_id that subscribe to *event*
    and enqueue a Celery delivery task for each.
    Called after scan/clinic completion (from async context).
    """
    from sqlalchemy import select
    from app.models.webhook import Webhook
    from app.workers.webhook_tasks import deliver_webhook

    result = await db.execute(
        select(Webhook).where(
            Webhook.namespace_id == namespace_id,
            Webhook.is_active == True,
        )
    )
    hooks = result.scalars().all()
    for hook in hooks:
        if event in (hook.events or []):
            deliver_webhook.delay(hook.id, event, payload)
