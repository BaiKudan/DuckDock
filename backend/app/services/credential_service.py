"""运行时凭证的加密存取。

引用(`credential_ref`)方案:
- ``db:<id>``  → `credential_records` 表中的 Fernet 密文(本服务管理,支持就地轮换)
- ``env:<NAME>`` → 部署环境变量(零密文落库的逃生门)
- 其它外部引用前缀(如 ``vault://``)本服务不解析,返回 None。

明文只在进程内存在:落库前加密、出接口前丢弃,绝不写日志/审计。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.control_plane import CredentialRecord

DB_REF_PREFIX = "db:"
ENV_REF_PREFIX = "env:"


class CredentialKeyMissing(RuntimeError):
    """DUCKDOCK_CREDENTIAL_KEY 未配置。"""


class CredentialNotFound(LookupError):
    """引用指向的密文记录不存在或无法用当前密钥解密。"""


def _fernet() -> Fernet:
    key = settings.DUCKDOCK_CREDENTIAL_KEY
    if not key:
        raise CredentialKeyMissing(
            "DUCKDOCK_CREDENTIAL_KEY is not configured. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt_credential(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_credential(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialNotFound("Stored credential cannot be decrypted with the configured key") from exc


def make_db_ref(record: CredentialRecord) -> str:
    return f"{DB_REF_PREFIX}{record.id}"


async def store_credential(
    db: AsyncSession,
    *,
    plaintext: str,
    name: str | None = None,
    created_by: int | None = None,
    record_id: int | None = None,
) -> CredentialRecord:
    """加密并保存凭证;给 record_id 则就地轮换(保持 credential_ref 不变)。"""
    ciphertext = encrypt_credential(plaintext)
    if record_id is not None:
        record = await db.get(CredentialRecord, record_id)
        if record is None:
            raise CredentialNotFound(f"Credential record {record_id} not found")
        record.ciphertext = ciphertext
        record.rotated_at = datetime.now(timezone.utc)
        if name:
            record.name = name
    else:
        record = CredentialRecord(name=name, ciphertext=ciphertext, created_by=created_by)
        db.add(record)
    await db.flush()
    return record


async def resolve_credential(db: AsyncSession, ref: str | None) -> str | None:
    """按引用取回明文(仅供服务端 Adapter 使用,严禁透出 API)。"""
    if not ref:
        return None
    if ref.startswith(DB_REF_PREFIX):
        raw_id = ref[len(DB_REF_PREFIX):]
        try:
            record_id = int(raw_id)
        except ValueError as exc:
            raise CredentialNotFound(f"Malformed credential ref: {ref!r}") from exc
        record = await db.get(CredentialRecord, record_id)
        if record is None:
            raise CredentialNotFound(f"Credential record {record_id} not found")
        return decrypt_credential(record.ciphertext)
    if ref.startswith(ENV_REF_PREFIX):
        return os.environ.get(ref[len(ENV_REF_PREFIX):])
    return None
