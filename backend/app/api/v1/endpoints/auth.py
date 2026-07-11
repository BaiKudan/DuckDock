import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from jwt.exceptions import PyJWTError as JWTError
from starlette.responses import RedirectResponse
from sqlalchemy import func, select

from app.core.deps import DB, CurrentUser
from app.core.config import settings
from app.core.ratelimit import RateLimiter, get_rate_limiter
from app.services.audit_service import audit
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
)
from app.models.iam import SSOProviderConfig, SSOProviderType
from app.models.user import AuthSource, SystemRole, User
from app.schemas.auth import (
    ExchangeTokenRequest,
    LDAPLoginRequest,
    LoginRequest,
    PublicSSOProviderOut,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from app.schemas.user import UserOut
from app.services.sso_service import (
    SSOAuthenticationError,
    SSOConfigurationError,
    authenticate_ldap_user,
    build_frontend_exchange_uri,
    build_oidc_authorize_url,
    create_sso_exchange_token,
    discover_oidc_metadata,
    exchange_oidc_code,
    fetch_oidc_claims,
    upsert_external_user,
    validate_sso_exchange_token,
    validate_sso_state_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _allocate_duckdock_enterprise_uid(db: DB) -> str:
    while True:
        candidate = f"duid_{secrets.token_hex(12)}"
        existing = (await db.execute(select(User.id).where(User.enterprise_uid == candidate))).scalar_one_or_none()
        if existing is None:
            return candidate


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    db: DB,
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    # SEC-03 / L0-SEC-RL-FAIL: throttle registration per ip+username (no-op unless
    # RATE_LIMIT_ENABLED). precheck rejects locked-out keys; only rejected (409) attempts
    # register against the budget so a successful registration never consumes it.
    client_ip = limiter.client_ip(request)
    await limiter.precheck("register", body.username, client_ip)
    existing = await db.execute(
        select(User).where(
            (User.username == body.username) | (User.email == body.email)
        )
    )
    if existing.scalar_one_or_none():
        await limiter.register_failure("register", body.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already taken",
        )
    is_bootstrap = (await db.execute(select(func.count(User.id)))).scalar_one() == 0
    user = User(
        username=body.username,
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        system_role=SystemRole.ADMIN if is_bootstrap else SystemRole.USER,
        enterprise_uid=await _allocate_duckdock_enterprise_uid(db),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    await audit(
        db,
        user=user,
        action="user.registered",
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "bootstrap_admin": is_bootstrap},
    )
    if is_bootstrap:
        await audit(
            db,
            user=user,
            action="user.bootstrap_admin",
            resource_type="user",
            resource_id=user.id,
            details={"username": user.username},
        )
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: DB,
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    # SEC-03 / L0-SEC-RL-FAIL: true failure-lockout throttle per ip+username
    # (no-op unless RATE_LIMIT_ENABLED). precheck rejects locked-out keys without incrementing;
    # only a verified auth failure registers against the budget, so a successful login is free.
    client_ip = limiter.client_ip(request)
    await limiter.precheck("login", body.username, client_ip)
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        await limiter.register_failure("login", body.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )
    user.last_login_at = datetime.now(timezone.utc)
    await audit(db, user=user, action="user.login")
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/sso/providers", response_model=list[PublicSSOProviderOut])
async def list_enabled_sso_providers(db: DB):
    rows = (
        await db.execute(
            select(SSOProviderConfig)
            .where(SSOProviderConfig.enabled.is_(True))
            .order_by(SSOProviderConfig.provider_type, SSOProviderConfig.name)
        )
    ).scalars().all()
    items: list[PublicSSOProviderOut] = []
    for row in rows:
        login_url = None
        if row.provider_type == SSOProviderType.OIDC:
            login_url = f"{settings.BACKEND_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/auth/sso/oidc/{row.id}/start"
        items.append(
            PublicSSOProviderOut(
                id=row.id,
                name=row.name,
                provider_type=row.provider_type.value,
                login_url=login_url,
            )
        )
    return items


@router.get("/sso/oidc/{provider_id}/start")
async def start_oidc_login(provider_id: int, db: DB, next: str | None = Query(default="/dashboard")):
    provider = (
        await db.execute(
            select(SSOProviderConfig).where(
                SSOProviderConfig.id == provider_id,
                SSOProviderConfig.provider_type == SSOProviderType.OIDC,
                SSOProviderConfig.enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC provider not found")
    try:
        metadata = await discover_oidc_metadata(provider)
        authorize_url = build_oidc_authorize_url(provider, metadata, next_path=next)
    except SSOConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to reach OIDC provider") from exc
    return RedirectResponse(authorize_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/sso/oidc/{provider_id}/callback")
async def complete_oidc_login(provider_id: int, code: str, state: str, db: DB):
    provider = (
        await db.execute(
            select(SSOProviderConfig).where(
                SSOProviderConfig.id == provider_id,
                SSOProviderConfig.provider_type == SSOProviderType.OIDC,
                SSOProviderConfig.enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC provider not found")

    try:
        state_payload = validate_sso_state_token(state, provider_id)
        metadata = await discover_oidc_metadata(provider)
        token_payload = await exchange_oidc_code(provider, metadata, code=code)
        claims = await fetch_oidc_claims(provider, metadata, token_payload)
        user = await upsert_external_user(db, provider=provider, source=AuthSource.OIDC, claims=claims)
        exchange_token = create_sso_exchange_token(user)
        await audit(
            db,
            user=user,
            action="user.login.oidc",
            details={"provider": provider.name, "provider_id": provider.id},
        )
        redirect_url = build_frontend_exchange_uri(exchange_token, state_payload.get("next"))
        return RedirectResponse(redirect_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    except SSOAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except SSOConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC login failed upstream") from exc


@router.post("/sso/ldap/login", response_model=TokenResponse)
async def login_with_ldap(
    body: LDAPLoginRequest,
    db: DB,
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    # SEC-03 / L0-SEC-RL-FAIL: true failure-lockout throttle per ip+username
    # (no-op unless RATE_LIMIT_ENABLED). Only verified auth failures register against the budget.
    client_ip = limiter.client_ip(request)
    await limiter.precheck("ldap_login", body.username, client_ip)
    provider = (
        await db.execute(
            select(SSOProviderConfig).where(
                SSOProviderConfig.id == body.provider_id,
                SSOProviderConfig.provider_type == SSOProviderType.LDAP,
                SSOProviderConfig.enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LDAP provider not found")
    try:
        user = await authenticate_ldap_user(
            db,
            provider=provider,
            username=body.username,
            password=body.password,
        )
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
        await audit(
            db,
            user=user,
            action="user.login.ldap",
            details={"provider": provider.name, "provider_id": provider.id},
        )
        return TokenResponse(
            access_token=create_access_token(user.id),
            refresh_token=create_refresh_token(user.id),
        )
    except SSOAuthenticationError as exc:
        await limiter.register_failure("ldap_login", body.username, client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except SSOConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/sso/exchange", response_model=TokenResponse)
async def exchange_sso_token(
    body: ExchangeTokenRequest,
    db: DB,
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    # SEC-03 / L0-SEC-RL-FAIL: throttle token exchange per ip (no-op unless RATE_LIMIT_ENABLED).
    # Only verified failures (bad token / unknown user) register against the budget.
    client_ip = limiter.client_ip(request)
    await limiter.precheck("sso_exchange", "", client_ip)
    try:
        user_id = validate_sso_exchange_token(body.token)
    except (SSOAuthenticationError, JWTError, ValueError, KeyError):
        await limiter.register_failure("sso_exchange", "", client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid SSO exchange token")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        await limiter.register_failure("sso_exchange", "", client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    user.last_login_at = datetime.now(timezone.utc)
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    db: DB,
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    # SEC-03 / L0-SEC-RL-FAIL: throttle refresh-token use per ip (no-op unless RATE_LIMIT_ENABLED).
    # Only verified failures (bad token / unknown user) register against the budget.
    client_ip = limiter.client_ip(request)
    await limiter.precheck("refresh", "", client_ip)
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise ValueError
        user_id = int(payload["sub"])
    except (JWTError, ValueError, KeyError):
        await limiter.register_failure("refresh", "", client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        await limiter.register_failure("refresh", "", client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    user.last_login_at = datetime.now(timezone.utc)
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUser):
    return current_user
