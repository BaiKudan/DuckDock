import asyncio
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import jwt
from jwt.exceptions import PyJWTError as JWTError
from ldap3 import ALL, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import create_signed_token, decode_token, hash_password
from app.models.iam import IdentityLink, Role, RoleBinding, RolePermission, RoleScope, SSOProviderConfig, SSORoleMapping
from app.models.user import AuthSource, SystemRole, User
from app.services.iam_service import ensure_builtin_rbac
from app.services.ssrf import ssrf_safe_request

# Outbound SSO calls are bounded so a hostile/slow IdP cannot hang a worker.
SSO_HTTP_TIMEOUT = 15.0

USERNAME_RE = re.compile(r"[^a-z0-9_-]+")
ENTERPRISE_UID_RE = re.compile(r"[^A-Za-z0-9_.@:-]+")
DEFAULT_LDAP_SEARCH_FILTER = (
    "(|(uid={username})(mail={username})(sAMAccountName={username})"
    "(userPrincipalName={username})(cn={username}))"
)


class SSOConfigurationError(ValueError):
    pass


class SSOAuthenticationError(ValueError):
    pass


def _frontend_redirect_target(next_path: str | None) -> str:
    if next_path and next_path.startswith("/"):
        return next_path
    return "/dashboard"


def build_oidc_callback_uri(provider_id: int) -> str:
    return (
        f"{settings.BACKEND_BASE_URL.rstrip('/')}"
        f"{settings.API_V1_PREFIX}/auth/sso/oidc/{provider_id}/callback"
    )


def build_frontend_exchange_uri(exchange_token: str, next_path: str | None) -> str:
    params = {"token": exchange_token}
    target = _frontend_redirect_target(next_path)
    if target:
        params["next"] = target
    return f"{settings.FRONTEND_BASE_URL.rstrip('/')}/login/sso/callback?{urlencode(params)}"


def create_sso_state_token(*, provider_id: int, next_path: str | None) -> str:
    return create_signed_token(
        {
            "type": "sso_state",
            "provider_id": provider_id,
            "next": _frontend_redirect_target(next_path),
        },
        expires_delta=timedelta(minutes=settings.SSO_STATE_EXPIRE_MINUTES),
    )


def validate_sso_state_token(token: str, provider_id: int) -> dict[str, Any]:
    payload = decode_token(token)
    token_provider_id = payload.get("provider_id")
    try:
        token_provider_id_int = int(str(token_provider_id))
    except (TypeError, ValueError) as exc:
        raise SSOAuthenticationError("Invalid SSO state") from exc
    if payload.get("type") != "sso_state" or token_provider_id_int != provider_id:
        raise SSOAuthenticationError("Invalid SSO state")
    return payload


def create_sso_exchange_token(user: User) -> str:
    return create_signed_token(
        {"type": "sso_exchange", "sub": str(user.id)},
        expires_delta=timedelta(minutes=settings.SSO_EXCHANGE_EXPIRE_MINUTES),
    )


def validate_sso_exchange_token(token: str) -> int:
    payload = decode_token(token)
    if payload.get("type") != "sso_exchange":
        raise SSOAuthenticationError("Invalid SSO exchange token")
    return int(payload["sub"])


async def discover_oidc_metadata(provider: SSOProviderConfig) -> dict[str, Any]:
    if not provider.issuer_url:
        raise SSOConfigurationError("OIDC issuer URL is not configured")
    discovery_url = (provider.extra_config or {}).get("discovery_url") or (
        provider.issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    )
    # SSRF guard: a tenant-controlled issuer/discovery URL must never reach an
    # internal/loopback/link-local/metadata endpoint. Resolves + validates every
    # address and pins the connection (no DNS-rebinding window).
    response = await ssrf_safe_request("GET", discovery_url, timeout=SSO_HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def build_oidc_authorize_url(provider: SSOProviderConfig, metadata: dict[str, Any], *, next_path: str | None) -> str:
    authorization_endpoint = metadata.get("authorization_endpoint")
    if not authorization_endpoint:
        raise SSOConfigurationError("OIDC authorization endpoint is missing")
    if not provider.client_id:
        raise SSOConfigurationError("OIDC client ID is not configured")
    state = create_sso_state_token(provider_id=provider.id, next_path=next_path)
    extra_config = provider.extra_config or {}
    params: dict[str, Any] = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": build_oidc_callback_uri(provider.id),
        "scope": extra_config.get("scopes") or "openid profile email",
        "state": state,
    }
    if extra_config.get("prompt"):
        params["prompt"] = extra_config["prompt"]
    if extra_config.get("audience"):
        params["audience"] = extra_config["audience"]
    return f"{authorization_endpoint}?{urlencode(params)}"


async def exchange_oidc_code(
    provider: SSOProviderConfig,
    metadata: dict[str, Any],
    *,
    code: str,
) -> dict[str, Any]:
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise SSOConfigurationError("OIDC token endpoint is missing")
    if not provider.client_id or not provider.client_secret:
        raise SSOConfigurationError("OIDC client credentials are incomplete")

    # SSRF guard: token_endpoint comes from provider-controlled discovery metadata.
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": build_oidc_callback_uri(provider.id),
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
    }
    response = await ssrf_safe_request("POST", token_endpoint, data=data, timeout=SSO_HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


async def _fetch_jwks(jwks_uri: str) -> dict[str, Any]:
    """Fetch a provider JWKS document through the SSRF guard with a timeout."""
    # SSRF guard: jwks_uri comes from provider-controlled discovery metadata.
    response = await ssrf_safe_request("GET", jwks_uri, timeout=SSO_HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


async def _verify_id_token(
    provider: SSOProviderConfig,
    metadata: dict[str, Any],
    id_token: str,
) -> dict[str, Any]:
    """Verify an OIDC id_token: signature (via JWKS) + issuer + audience + expiry.

    There is intentionally **no** unverified fallback — an id_token that cannot be
    cryptographically verified is rejected.
    """
    jwks_uri = metadata.get("jwks_uri")
    if not jwks_uri:
        raise SSOAuthenticationError("OIDC provider metadata is missing jwks_uri")
    if not provider.client_id:
        raise SSOConfigurationError("OIDC client ID is not configured")

    jwks = await _fetch_jwks(jwks_uri)
    issuer = metadata.get("issuer") or (provider.issuer_url or "").rstrip("/") or None
    allowed_algorithms = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
    try:
        header = jwt.get_unverified_header(id_token)
        kid = header.get("kid")
        algorithm = header.get("alg")
        if not kid or algorithm not in allowed_algorithms:
            raise SSOAuthenticationError("OIDC id_token has an invalid signing header")

        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        if not isinstance(keys, list):
            raise SSOAuthenticationError("OIDC provider returned an invalid JWKS")
        matching_keys = [
            key
            for key in keys
            if isinstance(key, dict)
            and key.get("kid") == kid
            and key.get("use", "sig") == "sig"
            and key.get("alg", algorithm) == algorithm
        ]
        if len(matching_keys) != 1:
            raise SSOAuthenticationError("OIDC id_token signing key was not found uniquely")
        signing_key = jwt.PyJWK.from_dict(matching_keys[0]).key
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=[algorithm],
            audience=provider.client_id,
            issuer=issuer,
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_exp": True,
                "verify_iss": issuer is not None,
            },
        )
    except (JWTError, TypeError, ValueError, KeyError) as exc:
        raise SSOAuthenticationError("OIDC id_token verification failed") from exc
    if not claims.get("sub"):
        raise SSOAuthenticationError("OIDC id_token has no subject")
    return claims


async def fetch_oidc_claims(
    provider: SSOProviderConfig,
    metadata: dict[str, Any],
    token_payload: dict[str, Any],
) -> dict[str, Any]:
    # An id_token, when present, is the trust anchor: it is signature-verified
    # against the provider JWKS first. userinfo is only used to enrich claims.
    id_token = token_payload.get("id_token")
    verified_claims: dict[str, Any] | None = None
    if id_token:
        verified_claims = await _verify_id_token(provider, metadata, id_token)

    userinfo_endpoint = metadata.get("userinfo_endpoint")
    access_token = token_payload.get("access_token")
    if userinfo_endpoint and access_token:
        # SSRF guard: userinfo_endpoint comes from provider-controlled metadata.
        response = await ssrf_safe_request(
            "GET",
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=SSO_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        claims = response.json()
        if isinstance(claims, dict) and claims.get("sub"):
            # When an id_token was verified, the userinfo sub must match it
            # (OIDC spec) — otherwise reject to avoid claim substitution.
            if verified_claims and claims.get("sub") != verified_claims.get("sub"):
                raise SSOAuthenticationError("OIDC userinfo subject mismatch")
            return claims

    if verified_claims is not None:
        return verified_claims
    raise SSOAuthenticationError("OIDC provider did not return usable claims")


def _claim_value(claims: dict[str, Any], key: str | None, defaults: list[str]) -> str | None:
    for candidate in ([key] if key else []) + defaults:
        if candidate and claims.get(candidate):
            value = claims[candidate]
            if isinstance(value, list | tuple | set):
                value = next((item for item in value if item), None)
            if value:
                return str(value).strip()
    return None


def _claim_values(claims: dict[str, Any], key: str) -> list[str]:
    value: Any = claims
    for part in key.split("."):
        if not isinstance(value, dict):
            return []
        value = value.get(part)
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        if "," in value:
            return [item.strip() for item in value.split(",") if item.strip()]
        return [value.strip()] if value.strip() else []
    return [str(value).strip()] if str(value).strip() else []


def _normalize_username(raw: str) -> str:
    value = USERNAME_RE.sub("-", raw.strip().lower()).strip("-_")
    if not value:
        value = "sso-user"
    if not value[0].isalpha():
        value = f"user-{value}"
    return value[:64]


def _normalize_enterprise_uid(raw: str | None) -> str | None:
    if not raw:
        return None
    value = ENTERPRISE_UID_RE.sub("-", raw.strip()).strip("-_.@:")
    return value[:96] or None


def _json_safe_claims(claims: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in claims.items():
        if isinstance(value, str | int | float | bool) or value is None:
            safe[key] = value
        elif isinstance(value, list | tuple | set):
            safe[key] = [str(item) for item in value]
        else:
            safe[key] = str(value)
    return safe


async def _allocate_unique_username(db: AsyncSession, base_value: str) -> str:
    base = _normalize_username(base_value)
    candidate = base
    index = 2
    while True:
        existing = (
            await db.execute(select(User.id).where(User.username == candidate))
        ).scalar_one_or_none()
        if existing is None:
            return candidate
        suffix = f"-{index}"
        candidate = f"{base[: max(1, 64 - len(suffix))]}{suffix}"
        index += 1


async def _allocate_unique_email(db: AsyncSession, base_value: str, provider_name: str) -> str:
    normalized = base_value.strip().lower()
    local_part, _, domain = normalized.partition("@")
    if not local_part or not domain:
        local_part = _normalize_username(base_value)
        domain = f"{_normalize_username(provider_name)}.sso.local"
    candidate = f"{local_part}@{domain}"
    index = 2
    while True:
        existing = (
            await db.execute(select(User.id).where(User.email == candidate))
        ).scalar_one_or_none()
        if existing is None:
            return candidate
        candidate = f"{local_part}+{index}@{domain}"
        index += 1


async def _allocate_enterprise_uid(db: AsyncSession, preferred: str | None = None) -> str:
    candidate = _normalize_enterprise_uid(preferred)
    if candidate:
        existing = (await db.execute(select(User.id).where(User.enterprise_uid == candidate))).scalar_one_or_none()
        if existing is None:
            return candidate
    while True:
        generated = f"duid_{secrets.token_hex(12)}"
        existing = (await db.execute(select(User.id).where(User.enterprise_uid == generated))).scalar_one_or_none()
        if existing is None:
            return generated


def _resolve_external_profile(
    provider: SSOProviderConfig,
    source: AuthSource,
    claims: dict[str, Any],
) -> dict[str, str | None]:
    mapping = provider.attribute_mapping or {}
    subject = _claim_value(claims, mapping.get("subject"), ["sub", "dn", "entry_dn", "distinguishedName", "uid"])
    if not subject:
        raise SSOAuthenticationError("Provider claims did not include a subject identifier")

    enterprise_uid = _normalize_enterprise_uid(
        _claim_value(
            claims,
            mapping.get("enterprise_uid"),
            ["enterprise_uid", "employee_id", "employeeNumber", "employee_no", "staff_id"],
        )
    )
    external_uid = _normalize_enterprise_uid(
        _claim_value(
            claims,
            mapping.get("external_uid"),
            ["external_uid", "employee_id", "employeeNumber", "employee_no", "uid", "userPrincipalName"],
        )
    )
    email = _claim_value(claims, mapping.get("email"), ["email", "mail", "userPrincipalName"])
    username = _claim_value(
        claims,
        mapping.get("username"),
        ["preferred_username", "username", "uid", "sAMAccountName", "cn", "email"],
    )
    full_name = _claim_value(claims, mapping.get("full_name"), ["name", "displayName", "cn"])
    if not full_name:
        given_name = _claim_value(claims, mapping.get("given_name"), ["given_name", "givenName"])
        family_name = _claim_value(claims, mapping.get("family_name"), ["family_name", "sn"])
        full_name = " ".join(part for part in [given_name, family_name] if part).strip() or None

    username_source = username or (email.split("@", 1)[0] if email else f"{provider.name}-{subject}")
    email_source = email or f"{_normalize_username(username_source)}@{_normalize_username(provider.name)}.sso.local"
    external_subject = f"{source.value}:{provider.id}:{subject}"
    return {
        "external_subject": external_subject,
        "provider_subject": subject,
        "enterprise_uid": enterprise_uid,
        "external_uid": external_uid or enterprise_uid,
        "username": username_source,
        "email": email_source,
        "full_name": full_name or username_source,
    }


def _issuer_for_provider(provider: SSOProviderConfig) -> str | None:
    if provider.issuer_url:
        return provider.issuer_url.rstrip("/")
    if provider.ldap_server_url:
        return provider.ldap_server_url
    return provider.name


def _role_scope_matches_mapping(mapping: SSORoleMapping) -> bool:
    role = mapping.role
    if role is None:
        return False
    has_namespace = mapping.namespace_id is not None
    has_org_unit = mapping.org_unit_id is not None
    if role.scope is RoleScope.NAMESPACE:
        return has_namespace and not has_org_unit
    if role.scope is RoleScope.ORG:
        return has_org_unit and not has_namespace
    return not has_namespace and not has_org_unit


def _permission_keys_for_mapping(mapping: SSORoleMapping) -> list[str]:
    role = mapping.role
    if role is None:
        return []
    return sorted(
        row.permission.key
        for row in role.permissions
        if row.permission is not None
    )


def _sso_binding_cache(provider_id: int, mapping_id: int, permission_keys: list[str]) -> dict[str, Any]:
    return {
        "keys": permission_keys,
        "managed_by": "sso_role_mapping",
        "provider_id": provider_id,
        "mapping_id": mapping_id,
    }


async def apply_sso_role_mappings(
    db: AsyncSession,
    *,
    provider: SSOProviderConfig,
    user: User,
    claims: dict[str, Any],
) -> list[int]:
    await ensure_builtin_rbac(db)
    mappings = (
        await db.execute(
            select(SSORoleMapping)
            .where(SSORoleMapping.provider_id == provider.id, SSORoleMapping.enabled.is_(True))
            .options(selectinload(SSORoleMapping.role).selectinload(Role.permissions).selectinload(RolePermission.permission))
            .order_by(SSORoleMapping.priority.asc(), SSORoleMapping.id.asc())
        )
    ).scalars().all()
    desired: dict[tuple[int, int | None, int | None], SSORoleMapping] = {}
    for mapping in mappings:
        values = set(_claim_values(claims, mapping.claim_name))
        if mapping.claim_value not in values:
            continue
        if not _role_scope_matches_mapping(mapping):
            continue
        key = (mapping.role_id, mapping.namespace_id, mapping.org_unit_id)
        desired.setdefault(key, mapping)

    existing_rows = (
        await db.execute(
            select(RoleBinding)
            .where(RoleBinding.user_id == user.id)
            .options(selectinload(RoleBinding.role).selectinload(Role.permissions).selectinload(RolePermission.permission))
        )
    ).scalars().all()
    managed = [
        row
        for row in existing_rows
        if isinstance(row.permission_cache, dict)
        and row.permission_cache.get("managed_by") == "sso_role_mapping"
        and row.permission_cache.get("provider_id") == provider.id
    ]
    existing_by_key = {
        (row.role_id, row.namespace_id, row.org_unit_id): row
        for row in existing_rows
    }

    desired_keys = set(desired)
    for row in managed:
        key = (row.role_id, row.namespace_id, row.org_unit_id)
        if key not in desired_keys:
            await db.delete(row)

    applied: list[int] = []
    for key, mapping in desired.items():
        permission_keys = _permission_keys_for_mapping(mapping)
        cache = _sso_binding_cache(provider.id, mapping.id, permission_keys)
        existing_binding = existing_by_key.get(key)
        if existing_binding is None:
            db.add(
                RoleBinding(
                    role_id=mapping.role_id,
                    user_id=user.id,
                    namespace_id=mapping.namespace_id,
                    org_unit_id=mapping.org_unit_id,
                    granted_by=None,
                    permission_cache=cache,
                )
            )
        elif (
            isinstance(existing_binding.permission_cache, dict)
            and existing_binding.permission_cache.get("managed_by") == "sso_role_mapping"
        ):
            existing_binding.permission_cache = cache
        applied.append(mapping.id)
    await db.flush()
    return applied


async def _upsert_identity_link(
    db: AsyncSession,
    *,
    provider: SSOProviderConfig,
    source: AuthSource,
    user: User,
    profile: dict[str, str | None],
    claims: dict[str, Any],
) -> IdentityLink:
    now = datetime.now(timezone.utc)
    subject = str(profile["provider_subject"])
    link = (
        await db.execute(
            select(IdentityLink).where(
                IdentityLink.provider_id == provider.id,
                IdentityLink.external_subject == subject,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        link = IdentityLink(
            user_id=user.id,
            provider_id=provider.id,
            source=source,
            external_subject=subject,
            created_at=now,
        )
        db.add(link)
    link.user_id = user.id
    link.source = source
    link.issuer = _issuer_for_provider(provider)
    link.external_uid = profile.get("external_uid")
    link.username = profile.get("username")
    link.email = profile.get("email")
    link.full_name = profile.get("full_name")
    link.claims_json = _json_safe_claims(claims)
    link.last_seen_at = now
    link.updated_at = now
    await db.flush()
    return link


async def upsert_external_user(
    db: AsyncSession,
    *,
    provider: SSOProviderConfig,
    source: AuthSource,
    claims: dict[str, Any],
) -> User:
    profile = _resolve_external_profile(provider, source, claims)
    subject = str(profile["provider_subject"])
    identity = (
        await db.execute(
            select(IdentityLink).where(
                IdentityLink.provider_id == provider.id,
                IdentityLink.external_subject == subject,
            )
        )
    ).scalar_one_or_none()
    existing: User | None = None
    if identity is not None:
        existing = await db.get(User, identity.user_id)
    if existing is None and profile.get("enterprise_uid"):
        existing = (
            await db.execute(select(User).where(User.enterprise_uid == profile["enterprise_uid"]))
        ).scalar_one_or_none()
    if existing is None:
        existing = (
            await db.execute(select(User).where(User.external_subject == profile["external_subject"]))
        ).scalar_one_or_none()
    if existing:
        existing.last_login_at = datetime.now(timezone.utc)
        existing.full_name = profile["full_name"] or existing.full_name
        existing.auth_source = source
        if not existing.enterprise_uid:
            existing.enterprise_uid = await _allocate_enterprise_uid(db, profile.get("enterprise_uid"))
        if not existing.external_subject:
            existing.external_subject = profile["external_subject"]
        if not existing.is_active:
            raise SSOAuthenticationError("Account is disabled")
        await _upsert_identity_link(db, provider=provider, source=source, user=existing, profile=profile, claims=claims)
        await apply_sso_role_mappings(db, provider=provider, user=existing, claims=claims)
        return existing

    username = await _allocate_unique_username(db, profile["username"] or "")
    email = await _allocate_unique_email(db, profile["email"] or "", provider.name)
    is_bootstrap = (await db.execute(select(func.count(User.id)))).scalar_one() == 0
    user = User(
        username=username,
        email=email,
        full_name=profile["full_name"],
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        auth_source=source,
        enterprise_uid=await _allocate_enterprise_uid(db, profile.get("enterprise_uid")),
        external_subject=profile["external_subject"],
        is_active=True,
        last_login_at=datetime.now(timezone.utc),
        system_role=SystemRole.ADMIN if is_bootstrap else SystemRole.USER,
    )
    db.add(user)
    await db.flush()
    await _upsert_identity_link(db, provider=provider, source=source, user=user, profile=profile, claims=claims)
    await apply_sso_role_mappings(db, provider=provider, user=user, claims=claims)
    return user


def _ldap_authenticate_sync(provider: SSOProviderConfig, username: str, password: str) -> dict[str, Any]:
    if not provider.ldap_server_url:
        raise SSOConfigurationError("LDAP server URL is not configured")
    if not provider.ldap_user_search_base:
        raise SSOConfigurationError("LDAP user search base is not configured")
    if not password:
        raise SSOAuthenticationError("Password is required")

    escaped_username = escape_filter_chars(username)
    search_filter_template = provider.ldap_user_search_filter or DEFAULT_LDAP_SEARCH_FILTER
    search_filter = search_filter_template.replace("{username}", escaped_username)
    attribute_mapping = provider.attribute_mapping or {}
    requested_attributes = {
        "mail",
        "cn",
        "displayName",
        "uid",
        "sAMAccountName",
        "userPrincipalName",
        "givenName",
        "sn",
    }
    requested_attributes.update(
        value for value in attribute_mapping.values() if isinstance(value, str) and value
    )

    try:
        server = Server(provider.ldap_server_url, get_info=ALL)
        bind_user = provider.ldap_bind_dn or None
        bind_password = provider.ldap_bind_password or None
        with Connection(
            server,
            user=bind_user,
            password=bind_password,
            auto_bind=True if bind_user or bind_password else False,
        ) as conn:
            if not conn.bound:
                conn.bind()
            conn.search(
                search_base=provider.ldap_user_search_base,
                search_filter=search_filter,
                attributes=list(requested_attributes),
            )
            if not conn.entries:
                raise SSOAuthenticationError("LDAP user not found")
            entry = conn.entries[0]
            user_dn = entry.entry_dn

        with Connection(server, user=user_dn, password=password, auto_bind=True):
            pass
    except LDAPException as exc:
        raise SSOAuthenticationError("LDAP authentication failed") from exc

    attributes = entry.entry_attributes_as_dict
    claims = {key: value[0] if isinstance(value, list) and value else value for key, value in attributes.items()}
    claims["entry_dn"] = entry.entry_dn
    claims.setdefault("cn", entry.entry_dn)
    return claims


async def authenticate_ldap_user(
    db: AsyncSession,
    *,
    provider: SSOProviderConfig,
    username: str,
    password: str,
) -> User:
    claims = await asyncio.to_thread(_ldap_authenticate_sync, provider, username, password)
    return await upsert_external_user(db, provider=provider, source=AuthSource.LDAP, claims=claims)
