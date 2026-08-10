"""FND-030/034 canonical metadata-only envelope tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.execution import AgentRunStatus, ContentCaptureMode
from app.schemas.execution import RunCompleteEnvelope, RunStartEnvelope
from app.services.envelope_canonicalization_service import (
    canonical_json,
    canonical_sha256,
)


def _start(**overrides) -> RunStartEnvelope:
    values = {
        "external_run_id": "run-1",
        "started_at": "2026-07-28T08:00:00+08:00",
        "source_schema": "duckdock-run-envelope",
        "source_schema_version": "1.0",
        "content_capture_mode": ContentCaptureMode.METADATA_ONLY,
        "metadata": {
            "service.name": "café",
            "deployment.environment": "dev",
            "operation.name": "agent.run",
        },
    }
    values.update(overrides)
    return RunStartEnvelope.model_validate(values)


def test_canonical_json_normalizes_key_order_nulls_time_unicode_and_numbers() -> None:
    first = _start(
        root_span_id=None,
        metadata={
            "operation.name": "agent.run",
            "deployment.environment": "dev",
            "service.name": "cafe\u0301",
            "agent.mode": 1.0,
        },
    )
    second = _start(
        started_at=datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc),
        metadata={
            "agent.mode": 1,
            "service.name": "café",
            "deployment.environment": "dev",
            "operation.name": "agent.run",
        },
    )

    assert canonical_json(first) == canonical_json(second)
    assert canonical_sha256(first) == canonical_sha256(second)
    assert '"started_at":"2026-07-28T00:00:00.000000Z"' in canonical_json(first)
    assert "root_span_id" not in canonical_json(first)


@pytest.mark.parametrize(
    "field",
    ["prompt", "messages", "completion", "tool_arguments", "tool_result"],
)
def test_run_start_rejects_raw_content_fields(field: str) -> None:
    payload = _start().model_dump(mode="json")
    payload[field] = "forbidden raw content"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunStartEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    "metadata",
    [
        {"prompt": "forbidden"},
        {"service.name": {"nested": "object"}},
        {"service.name": ["x"] * 101},
        {"service.name": "x" * 2001},
        {f"unknown.{index}": index for index in range(33)},
    ],
)
def test_run_metadata_rejects_unknown_deep_or_oversized_values(metadata) -> None:
    with pytest.raises(ValidationError):
        _start(metadata=metadata)


def test_completion_schema_is_terminal_and_metadata_only() -> None:
    completion = RunCompleteEnvelope(
        ended_at="2026-07-28T00:00:01Z",
        status=AgentRunStatus.SUCCEEDED,
        duration_ms=1000,
        step_count=1,
        metadata={"service.version": "1.2.3"},
    )
    assert completion.status == AgentRunStatus.SUCCEEDED

    with pytest.raises(ValidationError):
        RunCompleteEnvelope.model_validate(
            {
                **completion.model_dump(mode="json"),
                "status": "STARTED",
            }
        )
    with pytest.raises(ValidationError):
        RunCompleteEnvelope.model_validate(
            {
                **completion.model_dump(mode="json"),
                "tool_result": "raw result",
            }
        )


def test_timestamps_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _start(started_at="2026-07-28T00:00:00")
