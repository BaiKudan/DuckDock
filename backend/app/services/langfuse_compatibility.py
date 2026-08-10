from __future__ import annotations

from importlib import metadata


LANGFUSE_COMPATIBILITY_PROFILE = "langfuse-v4"
LANGFUSE_SERVER_BASELINE_VERSION = "4.1.0"
LANGFUSE_SDK_BASELINE_VERSION = "4.14.2"
LANGFUSE_SUPPORTED_SDK_MAJOR = 4
LANGFUSE_OTLP_INGESTION_VERSION = "4"
LANGFUSE_OBSERVATIONS_FIELDS = "core,basic,metadata"
LANGFUSE_EXPERIMENT_FIELDS = "core,metadata,scores"
LANGFUSE_RESULT_SCHEMA_NAME = "langfuse-experiment-result"
LANGFUSE_RESULT_SCHEMA_VERSION = "sdk-v4"


class LangfuseCompatibilityError(RuntimeError):
    pass


def installed_langfuse_sdk_version() -> str:
    try:
        return metadata.version("langfuse")
    except metadata.PackageNotFoundError as exc:
        raise LangfuseCompatibilityError(
            "Langfuse SDK is not installed"
        ) from exc


def require_langfuse_sdk_compatibility(
    *,
    expected_version: str = LANGFUSE_SDK_BASELINE_VERSION,
) -> str:
    installed_version = installed_langfuse_sdk_version()
    major_text = installed_version.partition(".")[0]
    try:
        major = int(major_text)
    except ValueError as exc:
        raise LangfuseCompatibilityError(
            f"Langfuse SDK version is malformed: {installed_version}"
        ) from exc
    if major != LANGFUSE_SUPPORTED_SDK_MAJOR:
        raise LangfuseCompatibilityError(
            "Langfuse compatibility profile "
            f"{LANGFUSE_COMPATIBILITY_PROFILE} requires SDK major "
            f"{LANGFUSE_SUPPORTED_SDK_MAJOR}; installed={installed_version}"
        )
    if installed_version != expected_version:
        raise LangfuseCompatibilityError(
            "Langfuse SDK version has not passed the DuckDock compatibility "
            f"gate: expected={expected_version}, installed={installed_version}"
        )
    return installed_version
