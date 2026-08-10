from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel


CANONICALIZER_VERSION = "duckdock-canonical-json-v1"


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value.astimezone(timezone.utc)
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON does not support non-finite numbers")
        if value.is_integer():
            return int(value)
        return value
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = unicodedata.normalize("NFC", str(raw_key))
            if key in result:
                raise ValueError("Unicode normalization produced a duplicate JSON key")
            result[key] = _normalize(raw_value)
        return result
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(envelope: BaseModel) -> str:
    payload = envelope.model_dump(mode="python", exclude_none=True)
    return json.dumps(
        _normalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(envelope: BaseModel) -> str:
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()
