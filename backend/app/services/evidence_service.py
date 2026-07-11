"""证据对象签名下载(FR-009 / specs/001 T013)。

对外签发**限时**预签名下载 URL(宪法原则 V:对外签名 URL 必须设过期)。

`EvidenceItem.object_uri` 形如 ``s3://{bucket}/{key}``;来自报告包内部的证据再带
``#<archive 内路径>`` 片段(见 report_pack_service)。S3/MinIO 只能对**整个对象**签名,
无法对 zip 内子路径签名——因此对带片段的证据,签名其所在归档对象,并在响应里回传
archive 内路径(``archive_path``),由客户端下载归档后自行提取。这一限制对调用方显式可见,
而不是悄悄给一个打不开的链接。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from botocore.exceptions import BotoCoreError, ClientError

from app.services.artifact_service import ArtifactStorageError, artifact_service

# 仅这些 scheme 是受管存储引用、可签名;其余(http/https/裸路径)一律拒签,避免泄露/给出死链。
_SIGNABLE_SCHEMES = {"s3", "minio"}


class EvidenceObjectError(Exception):
    """object_uri 不可签名:缺失、非受管存储引用,或落在未受管的桶里。"""


@dataclass(slots=True)
class ParsedEvidenceObject:
    bucket: str
    key: str
    archive_path: str | None  # zip 内路径(URI 片段);整对象签名时回传给客户端自行提取


def parse_storage_reference(object_uri: str) -> ParsedEvidenceObject:
    """解析 ``s3://bucket/key#archive/path`` → (bucket, key, archive_path)。

    不可签名的引用抛 ``EvidenceObjectError``(由端点转 422)。
    """
    base, _, fragment = object_uri.partition("#")
    parts = urlsplit(base)
    if parts.scheme not in _SIGNABLE_SCHEMES:
        raise EvidenceObjectError(
            f"evidence object scheme '{parts.scheme or '(none)'}' is not a signable storage reference"
        )
    bucket = parts.netloc
    # object_uri 由 f"s3://{bucket}/{object_key}" 直接拼成,path 段即存储时用的 S3 Key 原文
    # (key builder 已做 percent-escape)。**不要** unquote——否则 'my%20pack.zip' / '%23' /
    # 非 ASCII 会被解码成另一个对象名,签出指向从未上传过的对象的死链。
    key = parts.path.lstrip("/")
    if not bucket or not key:
        raise EvidenceObjectError("evidence object reference is missing a bucket or key")
    return ParsedEvidenceObject(bucket=bucket, key=key, archive_path=fragment or None)


def build_download_link(*, object_uri: str, expires_in: int | None = None) -> dict[str, Any]:
    """为证据对象签发限时下载链接。

    返回 ``generate_presigned_get_url`` 的字段(download_url/expires_in/expires_at)外加
    ``archive_path`` 与 ``is_archive_member``。expires_in 的上下界由 artifact_service clamp。
    可能抛 ``EvidenceObjectError``(不可签名)或 ``ArtifactStorageError``(存储不可用)。
    """
    parsed = parse_storage_reference(object_uri)
    if parsed.bucket != artifact_service.bucket:
        # 不把桶名(内部存储结构)写进异常文案,避免经 HTTP detail 外泄(原则 V)。
        raise EvidenceObjectError("evidence object is stored outside the managed artifact bucket")
    try:
        signed = artifact_service.generate_presigned_get_url(object_key=parsed.key, expires_in=expires_in)
    except (BotoCoreError, ClientError) as exc:
        # 签名/桶探测的底层 boto 异常统一收敛为 ArtifactStorageError,兑现本函数契约
        # (端点据此返回 502 而非 500)。
        raise ArtifactStorageError(f"failed to sign evidence object: {exc}") from exc
    return {
        **signed,
        "archive_path": parsed.archive_path,
        "is_archive_member": parsed.archive_path is not None,
    }
