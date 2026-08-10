from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings
from app.services.git_service import GitServiceError, git_service


class ArtifactStorageError(Exception):
    pass


class ArtifactStorageService:
    def __init__(self) -> None:
        self.bucket = settings.MINIO_BUCKET
        self._bucket_ensured = False
        self._data_client = None
        self._presign_client = None

    def is_configured(self) -> bool:
        return all(
            [
                settings.MINIO_ENDPOINT,
                settings.MINIO_ACCESS_KEY,
                settings.MINIO_SECRET_KEY,
                settings.MINIO_BUCKET,
            ]
        )

    def _endpoint_url(self, endpoint: str) -> str:
        clean = endpoint.strip().rstrip("/")
        if clean.startswith("http://") or clean.startswith("https://"):
            return clean
        scheme = "https" if settings.MINIO_SECURE else "http"
        return f"{scheme}://{clean}"

    def _build_client(self, endpoint: str):
        return boto3.client(
            "s3",
            endpoint_url=self._endpoint_url(endpoint),
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            region_name="us-east-1",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    @property
    def data_client(self):
        if self._data_client is None:
            self._data_client = self._build_client(settings.MINIO_ENDPOINT)
        return self._data_client

    @property
    def presign_client(self):
        if self._presign_client is None:
            public_endpoint = settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT
            self._presign_client = self._build_client(public_endpoint)
        return self._presign_client

    def _ensure_bucket(self) -> None:
        if self._bucket_ensured:
            return
        try:
            self.data_client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise ArtifactStorageError(f"Failed to access bucket '{self.bucket}': {exc}") from exc
            try:
                self.data_client.create_bucket(Bucket=self.bucket)
            except ClientError as create_exc:
                raise ArtifactStorageError(
                    f"Failed to create bucket '{self.bucket}': {create_exc}"
                ) from create_exc
        self._bucket_ensured = True

    def _segment(self, value: str) -> str:
        return quote(value, safe="-_.~")

    def bundle_object_key(self, namespace: str, skill_name: str, tag: str) -> str:
        return (
            "registry/namespaces/"
            f"{self._segment(namespace)}/skills/{self._segment(skill_name)}/versions/{self._segment(tag)}/bundle.tar.gz"
        )

    def manifest_object_key(self, namespace: str, skill_name: str, tag: str) -> str:
        return (
            "registry/namespaces/"
            f"{self._segment(namespace)}/skills/{self._segment(skill_name)}/versions/{self._segment(tag)}/manifest.json"
        )

    def report_pack_object_key(self, *, runtime_id: int, report_id: str, filename: str) -> str:
        safe_filename = self._segment(filename or "duckdock-pack-v1.zip")
        created = datetime.now(timezone.utc)
        return (
            "reports/"
            f"runtime-{runtime_id}/"
            f"{created:%Y/%m/%d}/"
            f"{self._segment(report_id)}/{safe_filename}"
        )

    def pack_import_staging_object_key(
        self,
        *,
        namespace_id: int,
        runtime_id: int,
        pack_id: str,
        sha256: str,
    ) -> str:
        return (
            "trajectory-imports/packs/"
            f"namespace-{namespace_id}/runtime-{runtime_id}/"
            f"{self._segment(pack_id)}/{sha256}.zip"
        )

    def pack_import_artifact_object_key(
        self,
        *,
        namespace_id: int,
        run_public_id: str,
        sha256: str,
        filename: str,
    ) -> str:
        return (
            "trajectory-imports/artifacts/"
            f"namespace-{namespace_id}/{self._segment(run_public_id)}/"
            f"{sha256}/{self._segment(filename)}"
        )

    def pack_export_object_key(
        self,
        *,
        namespace_id: int,
        runtime_id: int,
        run_public_id: str,
        pack_id: str,
    ) -> str:
        return (
            "trajectory-exports/packs/"
            f"namespace-{namespace_id}/runtime-{runtime_id}/"
            f"{self._segment(run_public_id)}/{self._segment(pack_id)}.zip"
        )

    def package_sbom_object_key(
        self,
        *,
        namespace_id: int,
        package_public_id: str,
        package_version: str,
        sha256: str,
    ) -> str:
        return (
            "package-registry/sboms/"
            f"namespace-{namespace_id}/{self._segment(package_public_id)}/"
            f"{self._segment(package_version)}/{sha256}.json"
        )

    def report_analysis_result_object_key(self, *, report_id: str, job_id: int, filename: str = "analysis-result.json") -> str:
        safe_filename = self._segment(filename)
        created = datetime.now(timezone.utc)
        return (
            "analysis/"
            f"{created:%Y/%m/%d}/"
            f"{self._segment(report_id)}/job-{job_id}/{safe_filename}"
        )

    def _tarball_from_files(
        self,
        skill_name: str,
        files: "dict[str, str] | dict[str, bytes] | dict[str, str | bytes]",
    ) -> bytes:
        """Build a gzipped tarball from a mapping of paths to content.

        Accepts either str (utf-8 encoded once) or bytes (passed through
        verbatim). Bytes input is REQUIRED to preserve binary files — see
        P0-3 handoff note. Text-only callers (JSON API publish path) are
        unaffected because str → utf-8 bytes is lossless for utf-8 content.
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for rel_path, content in sorted(files.items()):
                data = content if isinstance(content, bytes) else content.encode("utf-8")
                info = tarfile.TarInfo(name=f"{skill_name}/{rel_path}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def _delete_object(self, object_key: str) -> None:
        try:
            self.data_client.delete_object(Bucket=self.bucket, Key=object_key)
        except ClientError:
            pass

    def delete_object(self, object_key: str) -> None:
        """Best-effort deletion for a fully resolved internal object key."""

        self._delete_object(object_key)

    async def delete_object_async(self, object_key: str) -> None:
        await asyncio.to_thread(self.delete_object, object_key)

    def put_object_bytes(
        self,
        *,
        object_key: str,
        payload: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._ensure_bucket()
        try:
            self.data_client.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=payload,
                ContentType=content_type,
                Metadata=metadata or {},
            )
        except ClientError as exc:
            raise ArtifactStorageError(
                f"Failed to write object '{object_key}'"
            ) from exc
        return {
            "bucket": self.bucket,
            "object_key": object_key,
            "size_bytes": len(payload),
        }

    async def put_object_bytes_async(
        self,
        *,
        object_key: str,
        payload: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.put_object_bytes,
            object_key=object_key,
            payload=payload,
            content_type=content_type,
            metadata=metadata,
        )

    def _bucket_exists(self) -> bool:
        try:
            self.data_client.head_bucket(Bucket=self.bucket)
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in {"404", "NoSuchBucket", "NotFound"}:
                return False
            raise ArtifactStorageError(f"Failed to access bucket '{self.bucket}': {exc}") from exc

    def _delete_prefix(self, prefix: str) -> None:
        if not self.is_configured():
            return
        if not self._bucket_exists():
            return
        continuation_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation_token:
                params["ContinuationToken"] = continuation_token
            response = self.data_client.list_objects_v2(**params)
            contents = response.get("Contents", [])
            if contents:
                self.data_client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": item["Key"]} for item in contents], "Quiet": True},
                )
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

    def delete_version_artifacts(self, *, namespace: str, skill_name: str, tag: str) -> None:
        if not self.is_configured():
            return
        if not self._bucket_exists():
            return
        self._delete_object(self.bundle_object_key(namespace, skill_name, tag))
        self._delete_object(self.manifest_object_key(namespace, skill_name, tag))

    def delete_skill_artifacts(self, *, namespace: str, skill_name: str) -> None:
        prefix = (
            "registry/namespaces/"
            f"{self._segment(namespace)}/skills/{self._segment(skill_name)}/"
        )
        self._delete_prefix(prefix)

    def delete_namespace_artifacts(self, *, namespace: str) -> None:
        prefix = f"registry/namespaces/{self._segment(namespace)}/"
        self._delete_prefix(prefix)

    def _object_exists(self, object_key: str) -> bool:
        self._ensure_bucket()
        try:
            self.data_client.head_object(Bucket=self.bucket, Key=object_key)
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise ArtifactStorageError(f"Failed to inspect object '{object_key}': {exc}") from exc

    def head_object(self, object_key: str) -> dict[str, Any]:
        self._ensure_bucket()
        try:
            response = self.data_client.head_object(Bucket=self.bucket, Key=object_key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                raise ArtifactStorageError(f"Object '{object_key}' does not exist") from exc
            raise ArtifactStorageError(f"Failed to inspect object '{object_key}': {exc}") from exc
        return {
            "bucket": self.bucket,
            "object_key": object_key,
            "size_bytes": int(response.get("ContentLength") or 0),
            "content_type": response.get("ContentType"),
            "etag": response.get("ETag"),
            "metadata": response.get("Metadata") or {},
            "last_modified": response.get("LastModified"),
        }

    def read_object_bytes(self, object_key: str, *, max_bytes: int | None = None) -> bytes:
        self._ensure_bucket()
        try:
            response = self.data_client.get_object(Bucket=self.bucket, Key=object_key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                raise ArtifactStorageError(f"Object '{object_key}' does not exist") from exc
            raise ArtifactStorageError(f"Failed to read object '{object_key}': {exc}") from exc
        body = response["Body"]
        data = body.read()
        if max_bytes is not None and len(data) > max_bytes:
            raise ArtifactStorageError(f"Object '{object_key}' exceeds max read size")
        return data

    def hash_object(self, object_key: str) -> dict[str, int | str]:
        self._ensure_bucket()
        try:
            response = self.data_client.get_object(Bucket=self.bucket, Key=object_key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                raise ArtifactStorageError(f"Object '{object_key}' does not exist") from exc
            raise ArtifactStorageError(f"Failed to hash object '{object_key}': {exc}") from exc
        digest = hashlib.sha256()
        size = 0
        body = response["Body"]
        for chunk in iter(lambda: body.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        return {"sha256": digest.hexdigest(), "size_bytes": size}

    async def head_object_async(self, object_key: str) -> dict[str, Any]:
        """Async wrapper for :meth:`head_object`.

        The underlying boto/MinIO call is synchronous and blocking, so it is
        dispatched to a worker thread to avoid stalling the event loop
        (PUSH-03). Return value is identical to :meth:`head_object`.
        """
        return await asyncio.to_thread(self.head_object, object_key)

    async def read_object_bytes_async(
        self, object_key: str, *, max_bytes: int | None = None
    ) -> bytes:
        """Async wrapper for :meth:`read_object_bytes`.

        Offloads the blocking object download to a worker thread (PUSH-03).
        Return value is identical to :meth:`read_object_bytes`.
        """
        return await asyncio.to_thread(self.read_object_bytes, object_key, max_bytes=max_bytes)

    async def hash_object_async(self, object_key: str) -> dict[str, int | str]:
        """Async wrapper for :meth:`hash_object`.

        Offloads the blocking streamed read + hashing to a worker thread
        (PUSH-03). Return value is identical to :meth:`hash_object`.
        """
        return await asyncio.to_thread(self.hash_object, object_key)

    def _read_json(self, object_key: str) -> dict[str, Any] | None:
        self._ensure_bucket()
        try:
            response = self.data_client.get_object(Bucket=self.bucket, Key=object_key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ArtifactStorageError(f"Failed to read object '{object_key}': {exc}") from exc
        body = response["Body"].read()
        return json.loads(body.decode("utf-8"))

    def get_manifest(self, namespace: str, skill_name: str, tag: str) -> dict[str, Any] | None:
        return self._read_json(self.manifest_object_key(namespace, skill_name, tag))

    def publish_version_artifacts(
        self,
        *,
        namespace: str,
        skill_name: str,
        tag: str,
        commit_sha: str,
        description: str | None,
        status: str,
        skill_metadata: dict[str, Any] | None,
        changelog: str | None = None,
        publish_tags: list[str] | None = None,
        content_fingerprint: str | None = None,
        file_count: int | None = None,
        files: "dict[str, str] | dict[str, bytes] | dict[str, str | bytes]",
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise ArtifactStorageError("Artifact storage is not configured")

        self._ensure_bucket()

        bundle_key = self.bundle_object_key(namespace, skill_name, tag)
        manifest_key = self.manifest_object_key(namespace, skill_name, tag)
        tarball = self._tarball_from_files(skill_name, files)
        sha256 = hashlib.sha256(tarball).hexdigest()
        size_bytes = len(tarball)
        created_at = datetime.now(timezone.utc)
        manifest = {
            "schema_version": "duckdock-registry/v1",
            "generated_at": created_at.isoformat(),
            "namespace": namespace,
            "skill": skill_name,
            "tag": tag,
            "description": description,
            "commit_sha": commit_sha,
            "status": status,
            "files": sorted(files.keys()),
            "skill_metadata": skill_metadata or {},
            "changelog": changelog,
            "publish_tags": publish_tags or [],
            "content_fingerprint": content_fingerprint,
            "file_count": file_count if file_count is not None else len(files),
            "artifact": {
                "bucket": self.bucket,
                "object_key": bundle_key,
                "filename": f"{skill_name}-{tag}.tar.gz",
                "media_type": "application/gzip",
                "sha256": sha256,
                "size_bytes": size_bytes,
            },
        }

        try:
            self.data_client.put_object(
                Bucket=self.bucket,
                Key=bundle_key,
                Body=tarball,
                ContentType="application/gzip",
                Metadata={
                    "namespace": namespace,
                    "skill": skill_name,
                    "tag": tag,
                    "sha256": sha256,
                },
            )
            self.data_client.put_object(
                Bucket=self.bucket,
                Key=manifest_key,
                Body=json.dumps(manifest, ensure_ascii=True, indent=2).encode("utf-8"),
                ContentType="application/json",
                Metadata={
                    "namespace": namespace,
                    "skill": skill_name,
                    "tag": tag,
                },
            )
        except ClientError as exc:
            self._delete_object(bundle_key)
            self._delete_object(manifest_key)
            raise ArtifactStorageError(
                f"Failed to publish artifacts for {namespace}/{skill_name}:{tag}: {exc}"
            ) from exc

        return manifest

    def ensure_version_artifacts(
        self,
        *,
        namespace: str,
        skill_name: str,
        tag: str,
        commit_sha: str,
        description: str | None,
        status: str,
        skill_metadata: dict[str, Any] | None,
        changelog: str | None = None,
        publish_tags: list[str] | None = None,
        content_fingerprint: str | None = None,
        file_count: int | None = None,
        files: "dict[str, str] | dict[str, bytes] | dict[str, str | bytes] | None" = None,
    ) -> dict[str, Any]:
        bundle_key = self.bundle_object_key(namespace, skill_name, tag)
        manifest = self.get_manifest(namespace, skill_name, tag)
        if manifest is not None and self._object_exists(bundle_key):
            manifest_status = manifest.get("status")
            manifest_commit = manifest.get("commit_sha")
            manifest_description = manifest.get("description")
            manifest_metadata = manifest.get("skill_metadata") or {}
            expected_metadata = skill_metadata or {}
            if (
                manifest_status == status
                and manifest_commit == commit_sha
                and manifest_description == description
                and manifest_metadata == expected_metadata
                and manifest.get("changelog") == changelog
                and (manifest.get("publish_tags") or []) == (publish_tags or [])
                and manifest.get("content_fingerprint") == content_fingerprint
                and manifest.get("file_count") == (file_count if file_count is not None else manifest.get("file_count"))
            ):
                return manifest

        if manifest is not None and not self._object_exists(bundle_key):
            self._delete_object(self.manifest_object_key(namespace, skill_name, tag))

        if manifest is not None and files is None:
            try:
                # Bytes variant preserves binary file fidelity (P0-3).
                files = git_service.get_version_files_bytes(namespace, skill_name, tag)
            except GitServiceError as exc:
                raise ArtifactStorageError(str(exc)) from exc

        if manifest is not None and files is not None:
            return self.publish_version_artifacts(
                namespace=namespace,
                skill_name=skill_name,
                tag=tag,
                commit_sha=commit_sha,
                description=description,
                status=status,
                skill_metadata=skill_metadata,
                changelog=changelog,
                publish_tags=publish_tags,
                content_fingerprint=content_fingerprint,
                file_count=file_count,
                files=files,
            )

        if manifest is not None:
            return manifest

        if files is None:
            try:
                # Bytes variant preserves binary file fidelity (P0-3).
                files = git_service.get_version_files_bytes(namespace, skill_name, tag)
            except GitServiceError as exc:
                raise ArtifactStorageError(str(exc)) from exc

        return self.publish_version_artifacts(
            namespace=namespace,
            skill_name=skill_name,
            tag=tag,
            commit_sha=commit_sha,
            description=description,
            status=status,
            skill_metadata=skill_metadata,
            changelog=changelog,
            publish_tags=publish_tags,
            content_fingerprint=content_fingerprint,
            file_count=file_count,
            files=files,
        )

    def _expires_in(self, expires_in: int | None) -> int:
        requested = expires_in or settings.REGISTRY_SIGNED_URL_EXPIRE_SECONDS
        return max(60, min(requested, settings.REGISTRY_SIGNED_URL_MAX_SECONDS))

    def generate_signed_urls(
        self,
        *,
        namespace: str,
        skill_name: str,
        tag: str,
        expires_in: int | None = None,
    ) -> dict[str, Any]:
        self._ensure_bucket()
        ttl = self._expires_in(expires_in)
        bundle_key = self.bundle_object_key(namespace, skill_name, tag)
        manifest_key = self.manifest_object_key(namespace, skill_name, tag)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        return {
            "artifact_url": self.presign_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": bundle_key},
                ExpiresIn=ttl,
                HttpMethod="GET",
            ),
            "manifest_url": self.presign_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": manifest_key},
                ExpiresIn=ttl,
                HttpMethod="GET",
            ),
            "expires_in": ttl,
            "expires_at": expires_at,
        }

    def generate_presigned_put_url(
        self,
        *,
        object_key: str,
        content_type: str = "application/zip",
        expires_in: int | None = None,
    ) -> dict[str, Any]:
        self._ensure_bucket()
        ttl = self._expires_in(expires_in or settings.REPORT_UPLOAD_URL_EXPIRE_SECONDS)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        return {
            "upload_url": self.presign_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": object_key,
                    "ContentType": content_type,
                },
                ExpiresIn=ttl,
                HttpMethod="PUT",
            ),
            "expires_in": ttl,
            "expires_at": expires_at,
        }

    def initiate_multipart_upload(
        self,
        *,
        object_key: str,
        content_type: str = "application/zip",
    ) -> str:
        self._ensure_bucket()
        try:
            result = self.data_client.create_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                ContentType=content_type,
            )
        except ClientError as exc:
            raise ArtifactStorageError(
                f"Failed to initiate multipart upload for '{object_key}'"
            ) from exc
        upload_id = result.get("UploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise ArtifactStorageError("Artifact storage returned no upload ID")
        return upload_id

    def generate_presigned_upload_part_url(
        self,
        *,
        object_key: str,
        upload_id: str,
        part_number: int,
        expires_in: int | None = None,
    ) -> dict[str, Any]:
        self._ensure_bucket()
        ttl = self._expires_in(
            expires_in or settings.REPORT_UPLOAD_URL_EXPIRE_SECONDS
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        return {
            "upload_url": self.presign_client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self.bucket,
                    "Key": object_key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=ttl,
                HttpMethod="PUT",
            ),
            "expires_in": ttl,
            "expires_at": expires_at,
        }

    def list_multipart_parts(
        self,
        *,
        object_key: str,
        upload_id: str,
    ) -> list[dict[str, Any]]:
        self._ensure_bucket()
        parts: list[dict[str, Any]] = []
        marker = 0
        try:
            while True:
                response = self.data_client.list_parts(
                    Bucket=self.bucket,
                    Key=object_key,
                    UploadId=upload_id,
                    PartNumberMarker=marker,
                )
                for part in response.get("Parts", []):
                    parts.append(
                        {
                            "part_number": int(part["PartNumber"]),
                            "etag": str(part["ETag"]),
                            "size_bytes": int(part["Size"]),
                        }
                    )
                if not response.get("IsTruncated"):
                    break
                marker = int(response.get("NextPartNumberMarker", 0))
        except ClientError as exc:
            raise ArtifactStorageError(
                f"Failed to list multipart upload for '{object_key}'"
            ) from exc
        return parts

    def complete_multipart_upload(
        self,
        *,
        object_key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> None:
        self._ensure_bucket()
        try:
            self.data_client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                UploadId=upload_id,
                MultipartUpload={
                    "Parts": [
                        {
                            "PartNumber": int(part["part_number"]),
                            "ETag": str(part["etag"]),
                        }
                        for part in parts
                    ]
                },
            )
        except ClientError as exc:
            raise ArtifactStorageError(
                f"Failed to complete multipart upload for '{object_key}'"
            ) from exc

    def abort_multipart_upload(
        self,
        *,
        object_key: str,
        upload_id: str,
    ) -> None:
        try:
            self.data_client.abort_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                UploadId=upload_id,
            )
        except ClientError:
            pass

    def generate_presigned_get_url(
        self,
        *,
        object_key: str,
        expires_in: int | None = None,
    ) -> dict[str, Any]:
        self._ensure_bucket()
        ttl = self._expires_in(expires_in or settings.REPORT_UPLOAD_URL_EXPIRE_SECONDS)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        return {
            "download_url": self.presign_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": object_key},
                ExpiresIn=ttl,
                HttpMethod="GET",
            ),
            "expires_in": ttl,
            "expires_at": expires_at,
        }


artifact_service = ArtifactStorageService()
