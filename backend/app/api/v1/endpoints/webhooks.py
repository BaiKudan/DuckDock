from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import DB, CurrentUser, get_robot_account, require_namespace_integrations_manager
from app.models.namespace import Namespace
from app.models.webhook import WEBHOOK_EVENTS, Webhook, WebhookDelivery
from app.schemas.webhook import WebhookCreate, WebhookDeliveryOut, WebhookOut, WebhookUpdate
from app.services.audit_service import audit

router = APIRouter(tags=["webhooks"])


async def _get_namespace(ns_name: str, db: DB) -> Namespace:
    result = await db.execute(
        select(Namespace).where(Namespace.name == ns_name, Namespace.deleted_at.is_(None))
    )
    namespace = result.scalar_one_or_none()
    if not namespace:
        raise HTTPException(404, "Namespace not found")
    return namespace


@router.get("/webhooks/events", response_model=list[str])
async def list_known_events(current_user: CurrentUser):
    return WEBHOOK_EVENTS


@router.post(
    "/namespaces/{ns_name}/webhooks",
    response_model=WebhookOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook(ns_name: str, body: WebhookCreate, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot manage webhooks")

    namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, namespace.id, db)

    hook = Webhook(
        namespace_id=namespace.id,
        name=body.name,
        url=body.url,
        secret=body.secret,
        events=body.events,
        is_active=body.is_active,
        created_by=current_user.id,
    )
    db.add(hook)
    await db.flush()
    await db.refresh(hook)
    await audit(
        db,
        user=current_user,
        action="webhook.created",
        resource_type="webhook",
        resource_id=hook.id,
        namespace_id=namespace.id,
        details={"name": hook.name, "events": hook.events},
    )
    return hook


@router.get("/namespaces/{ns_name}/webhooks", response_model=list[WebhookOut])
async def list_webhooks(ns_name: str, db: DB, current_user: CurrentUser):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, namespace.id, db)

    result = await db.execute(
        select(Webhook).where(Webhook.namespace_id == namespace.id).order_by(Webhook.id)
    )
    return result.scalars().all()


@router.patch("/namespaces/{ns_name}/webhooks/{hook_id}", response_model=WebhookOut)
async def update_webhook(
    ns_name: str,
    hook_id: int,
    body: WebhookUpdate,
    db: DB,
    current_user: CurrentUser,
):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot manage webhooks")

    namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, namespace.id, db)

    result = await db.execute(
        select(Webhook).where(Webhook.id == hook_id, Webhook.namespace_id == namespace.id)
    )
    hook = result.scalar_one_or_none()
    if not hook:
        raise HTTPException(404, "Webhook not found")

    if body.name is not None:
        hook.name = body.name
    if body.url is not None:
        hook.url = body.url
    if body.secret is not None:
        hook.secret = body.secret
    if body.events is not None:
        hook.events = body.events
    if body.is_active is not None:
        hook.is_active = body.is_active

    db.add(hook)
    await db.flush()
    await db.refresh(hook)
    await audit(
        db,
        user=current_user,
        action="webhook.updated",
        resource_type="webhook",
        resource_id=hook.id,
        namespace_id=namespace.id,
        details={"name": hook.name, "events": hook.events, "is_active": hook.is_active},
    )
    return hook


@router.delete("/namespaces/{ns_name}/webhooks/{hook_id}", status_code=204)
async def delete_webhook(ns_name: str, hook_id: int, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot manage webhooks")

    namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, namespace.id, db)

    result = await db.execute(
        select(Webhook).where(Webhook.id == hook_id, Webhook.namespace_id == namespace.id)
    )
    hook = result.scalar_one_or_none()
    if not hook:
        raise HTTPException(404, "Webhook not found")

    await audit(
        db,
        user=current_user,
        action="webhook.deleted",
        resource_type="webhook",
        resource_id=hook.id,
        namespace_id=namespace.id,
        details={"name": hook.name},
    )
    await db.delete(hook)


@router.get(
    "/namespaces/{ns_name}/webhooks/{hook_id}/deliveries",
    response_model=list[WebhookDeliveryOut],
)
async def list_deliveries(
    ns_name: str,
    hook_id: int,
    skip: int = 0,
    limit: int = 50,
    db: DB = None,
    current_user: CurrentUser = None,
):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, namespace.id, db)

    hook_result = await db.execute(
        select(Webhook).where(Webhook.id == hook_id, Webhook.namespace_id == namespace.id)
    )
    if not hook_result.scalar_one_or_none():
        raise HTTPException(404, "Webhook not found")

    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == hook_id)
        .order_by(WebhookDelivery.attempted_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()
