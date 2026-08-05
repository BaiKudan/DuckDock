"""Bounded, dependency-free ATIF JSON validation for Pack preflight.

This intentionally does not normalize or persist trajectory content. It only
recognizes supported ATIF envelope versions and exposes content-policy signals
to the importer before any final object is written.
"""

from __future__ import annotations

import json
from typing import Any, Mapping


SUPPORTED_ATIF_VERSIONS = {
    "ATIF-v1.0",
    "ATIF-v1.1",
    "ATIF-v1.2",
    "ATIF-v1.3",
    "ATIF-v1.4",
    "ATIF-v1.5",
    "ATIF-v1.6",
    "ATIF-v1.7",
}
MAX_ATIF_DEPTH = 32
MAX_ATIF_STEPS = 100_000
_CANARY_MARKERS = ("secret_canary", "canary_do_not_export")
_CONTENT_KEYS = {
    "prompt",
    "system_prompt",
    "message",
    "messages",
    "conversation",
    "completion",
    "response",
    "response_text",
    "reasoning",
    "reasoning_content",
    "chain_of_thought",
    "tool_arguments",
    "arguments",
    "tool_result",
    "observation",
    "content",
    "raw_content",
    "logs",
    "stack_trace",
    "file_body",
    "image",
    "audio",
    "authorization",
    "cookie",
    "secret",
}


class AtifValidationError(ValueError):
    pass


class AtifSchemaUnsupportedError(AtifValidationError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AtifValidationError("ATIF JSON contains duplicate keys")
        result[key] = value
    return result


def _assert_bounded(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_ATIF_DEPTH:
        raise AtifValidationError("ATIF JSON nesting exceeds the limit")
    if isinstance(value, dict):
        for child in value.values():
            _assert_bounded(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _assert_bounded(child, depth=depth + 1)


def contains_secret_canary(payload: bytes) -> bool:
    lowered = payload.lower()
    return any(marker.encode("ascii") in lowered for marker in _CANARY_MARKERS)


def contains_content(value: Any) -> bool:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = raw_key.lower().replace("-", "_").replace(".", "_")
            if key in _CONTENT_KEYS and child not in (None, "", [], {}):
                return True
            if contains_content(child):
                return True
        return False
    if isinstance(value, list):
        return any(contains_content(child) for child in value)
    return False


class AtifTrajectoryCodec:
    """Minimal TrajectoryCodecPort implementation for ATIF v1.x."""

    def encode(self, document: Mapping[str, Any]) -> bytes:
        schema_version = document.get("schema_version")
        if schema_version not in SUPPORTED_ATIF_VERSIONS:
            raise AtifSchemaUnsupportedError(
                "ATIF schema version is unsupported"
            )
        return json.dumps(
            dict(document),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def decode(self, payload: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AtifValidationError("ATIF payload is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise AtifValidationError(
                "ATIF trajectory must be a JSON object"
            )
        _assert_bounded(decoded)
        schema_version = decoded.get("schema_version")
        if not isinstance(schema_version, str):
            raise AtifValidationError(
                "ATIF trajectory is missing schema_version"
            )
        if schema_version not in SUPPORTED_ATIF_VERSIONS:
            raise AtifSchemaUnsupportedError(
                "ATIF schema version is unsupported"
            )
        agent = decoded.get("agent")
        steps = decoded.get("steps")
        if not isinstance(agent, dict) or not isinstance(steps, list):
            raise AtifValidationError(
                "ATIF trajectory requires agent and steps"
            )
        if len(steps) > MAX_ATIF_STEPS:
            raise AtifValidationError("ATIF step count exceeds the limit")
        return decoded


atif_trajectory_codec = AtifTrajectoryCodec()
