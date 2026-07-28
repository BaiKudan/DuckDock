"""运行时凭证加密存取测试(specs/001 FR-016 · T011 · 宪法原则 V)。

覆盖:Fernet 往返、缺密钥报错、db:/env:/未知引用解析、就地轮换,
以及 create/update runtime 端点的"明文进、密文存、引用出、永不回传"。
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.api.v1.endpoints.control_plane import create_runtime, update_runtime
from app.core.config import settings
from app.models.control_plane import CredentialRecord, RuntimeProvider
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.schemas.control_plane import RuntimeInstanceCreate, RuntimeInstanceOut, RuntimeInstanceUpdate
from app.services.credential_service import (
    CredentialKeyMissing,
    CredentialNotFound,
    decrypt_credential,
    encrypt_credential,
    make_db_ref,
    resolve_credential,
    store_credential,
)


@pytest.fixture
def credential_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "DUCKDOCK_CREDENTIAL_KEY", key)
    return key


async def _make_admin(session, username="cred-admin"):
    user = User(
        username=username,
        email=f"{username}@duckdock-ai.com",
        hashed_password="x",
        system_role=SystemRole.ADMIN,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_namespace(session, owner: User, suffix: str) -> Namespace:
    namespace = Namespace(name=f"cred-{suffix}", owner_id=owner.id)
    session.add(namespace)
    await session.flush()
    return namespace


def test_encrypt_decrypt_round_trip(credential_key):
    token = encrypt_credential("gw-token-123")
    assert token != "gw-token-123"
    assert decrypt_credential(token) == "gw-token-123"


def test_missing_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "DUCKDOCK_CREDENTIAL_KEY", "")
    with pytest.raises(CredentialKeyMissing):
        encrypt_credential("x")


async def test_store_and_resolve_db_ref(async_session, credential_key):
    record = await store_credential(async_session, plaintext="s3cr3t", name="runtime:oc")
    assert record.ciphertext != "s3cr3t"
    assert await resolve_credential(async_session, make_db_ref(record)) == "s3cr3t"


async def test_rotate_in_place_keeps_ref(async_session, credential_key):
    record = await store_credential(async_session, plaintext="v1")
    ref = make_db_ref(record)
    await store_credential(async_session, plaintext="v2", record_id=record.id)
    assert await resolve_credential(async_session, ref) == "v2"
    assert record.rotated_at is not None


async def test_env_ref_and_unknown_scheme(async_session, monkeypatch):
    monkeypatch.setenv("DUCKDOCK_TEST_GW_TOKEN", "from-env")
    assert await resolve_credential(async_session, "env:DUCKDOCK_TEST_GW_TOKEN") == "from-env"
    assert await resolve_credential(async_session, "vault://duckdock/demo") is None
    assert await resolve_credential(async_session, None) is None
    with pytest.raises(CredentialNotFound):
        await resolve_credential(async_session, "db:999999")


async def test_create_runtime_encrypts_and_never_returns_plaintext(async_session, credential_key):
    admin = await _make_admin(async_session)
    namespace = await _make_namespace(async_session, admin, "create")
    runtime = await create_runtime(
        RuntimeInstanceCreate(
            namespace_id=namespace.id,
            provider=RuntimeProvider.OPENCLAW,
            name="oc-prod",
            credential="tok-plain-123",
        ),
        async_session,
        admin,
    )

    assert (runtime.credential_ref or "").startswith("db:")
    record = (await async_session.execute(select(CredentialRecord))).scalar_one()
    assert "tok-plain-123" not in record.ciphertext
    # 任何 API 出参不得携带明文(FR-016)
    assert "tok-plain-123" not in RuntimeInstanceOut.model_validate(runtime).model_dump_json()
    # 服务端 Adapter 可解析回明文
    assert await resolve_credential(async_session, runtime.credential_ref) == "tok-plain-123"


async def test_update_runtime_rotates_credential(async_session, credential_key):
    admin = await _make_admin(async_session, username="cred-admin2")
    namespace = await _make_namespace(async_session, admin, "rotate")
    runtime = await create_runtime(
        RuntimeInstanceCreate(
            namespace_id=namespace.id,
            provider=RuntimeProvider.JVS,
            name="jvs-1",
            credential="old-token",
        ),
        async_session,
        admin,
    )
    ref_before = runtime.credential_ref

    await update_runtime(runtime.id, RuntimeInstanceUpdate(credential="new-token"), async_session, admin)

    assert runtime.credential_ref == ref_before  # 就地轮换,引用不变
    assert await resolve_credential(async_session, runtime.credential_ref) == "new-token"


async def test_update_runtime_attaches_credential_when_ref_was_legacy(async_session, credential_key):
    admin = await _make_admin(async_session, username="cred-admin3")
    namespace = await _make_namespace(async_session, admin, "legacy")
    runtime = await create_runtime(
        RuntimeInstanceCreate(
            namespace_id=namespace.id,
            provider=RuntimeProvider.CUSTOM,
            name="legacy",
            credential_ref="vault://duckdock/demo",
        ),
        async_session,
        admin,
    )

    await update_runtime(runtime.id, RuntimeInstanceUpdate(credential="fresh"), async_session, admin)

    assert (runtime.credential_ref or "").startswith("db:")  # 由 legacy 引用切换到密文记录
    assert await resolve_credential(async_session, runtime.credential_ref) == "fresh"
