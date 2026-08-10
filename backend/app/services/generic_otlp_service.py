"""Trusted, metadata-only Generic OTLP trace reconciliation.

The Collector sends standard OTLP/HTTP JSON only after receiver
authentication, governance-key replacement and content filtering. This module
still treats every OTLP attribute as untrusted: tenant identity comes from the
Reporter credential, raw spans are never persisted, and ambiguous mappings are
quarantined instead of merged.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import (
    AgentRun,
    AgentRunStatus,
    ContentCaptureMode,
    TrustLevel,
    TrustSource,
)
from app.models.telemetry import (
    GenericTraceProjection,
    GenericTraceProjectionStatus,
    TelemetrySink,
    TelemetrySinkStatus,
    TraceBackendRef,
    TraceBackendRefStatus,
)
from app.schemas.execution import RunCompleteEnvelope, RunStartEnvelope
from app.services.audit_service import audit
from app.services.execution_service import complete_run, start_run
from app.services.reporter_identity_service import ReporterExecutionIdentity


GENERIC_OTLP_SOURCE_SCHEMA = "otlp-traces"
GENERIC_OTLP_SOURCE_SCHEMA_VERSION = "1.0"
GENERIC_OTLP_NORMALIZER_VERSION = "duckdock-generic-otlp-v1"
MAX_OTLP_SPANS = 2_000
TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
EXTERNAL_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RUN_PUBLIC_ID_RE = re.compile(r"^run_[0-9a-f]{32}$")
_FORBIDDEN_KEY_FRAGMENTS = (
    "prompt",
    "systemprompt",
    "messages",
    "conversation",
    "completion",
    "responsetext",
    "toolarguments",
    "toolresult",
    "chainofthought",
    "reasoningcontent",
    "rawcontent",
    "filebody",
    "authorization",
    "cookie",
    "credential",
    "apikey",
    "accesstoken",
    "refreshtoken",
)
_CANARY_FRAGMENTS = (
    "secret_canary",
    "canary_do_not_export",
)


class GenericOtlpError(ValueError):
    pass


class GenericOtlpPayloadError(GenericOtlpError):
    pass


class GenericOtlpSinkError(GenericOtlpError):
    pass


@dataclass(slots=True)
class _ParsedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    external_run_id: str | None
    run_public_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    failed: bool
    policy_reason: str | None


@dataclass(slots=True)
class GenericTraceCandidate:
    trace_id: str
    span_count: int
    candidate_root_count: int
    root_span_id: str | None
    external_run_id: str | None
    run_public_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    failed: bool
    quarantine_reason: str | None = None


@dataclass(slots=True)
class GenericOtlpParseResult:
    candidates: list[GenericTraceCandidate] = field(default_factory=list)
    rejected_spans: int = 0
    rejection_reason: str | None = None


@dataclass(slots=True)
class GenericOtlpReconcileResult:
    projections: list[GenericTraceProjection] = field(default_factory=list)
    rejected_spans: int = 0
    rejection_reason: str | None = None


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_forbidden_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return any(fragment in normalized for fragment in _FORBIDDEN_KEY_FRAGMENTS)


def _contains_canary(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return any(fragment in lowered for fragment in _CANARY_FRAGMENTS)
    if isinstance(value, dict):
        return any(
            _contains_canary(key) or _contains_canary(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_canary(child) for child in value)
    return False


def _attribute_string(attributes: Any, wanted_key: str) -> str | None:
    if not isinstance(attributes, list):
        return None
    for item in attributes:
        if not isinstance(item, dict) or item.get("key") != wanted_key:
            continue
        value = item.get("value")
        if not isinstance(value, dict):
            return None
        candidate = value.get("stringValue")
        if isinstance(candidate, str):
            return candidate
        return None
    return None


def _attribute_policy_reason(attributes: Any) -> str | None:
    if not isinstance(attributes, list):
        return "invalid_otlp_attributes"
    for item in attributes:
        if not isinstance(item, dict):
            return "invalid_otlp_attributes"
        key = item.get("key")
        if not isinstance(key, str):
            return "invalid_otlp_attributes"
        if _contains_canary(item):
            return "secret_canary_detected"
        if _is_forbidden_key(key):
            return "content_policy_rejected"
    return None


def _unix_nano(value: Any) -> datetime | None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    try:
        nanoseconds = int(value)
    except (TypeError, ValueError):
        return None
    if nanoseconds < 0:
        return None
    try:
        return datetime.fromtimestamp(
            nanoseconds / 1_000_000_000,
            tz=timezone.utc,
        )
    except (OverflowError, OSError, ValueError):
        return None


def _safe_external_run_id(
    attributes: Any,
) -> tuple[str | None, str | None]:
    value = _attribute_string(attributes, "duckdock.external_run_id")
    if value is None:
        return None, None
    if EXTERNAL_RUN_ID_RE.fullmatch(value) is None:
        return None, "invalid_external_run_id"
    return value, None


def _safe_run_public_id(
    attributes: Any,
) -> tuple[str | None, str | None]:
    value = _attribute_string(attributes, "duckdock.run.public_id")
    if value is None:
        return None, None
    if RUN_PUBLIC_ID_RE.fullmatch(value) is None:
        return None, "invalid_run_public_id"
    return value, None


def _span_policy_reason(span: dict[str, Any]) -> str | None:
    if _contains_canary(span):
        return "secret_canary_detected"
    attribute_reason = _attribute_policy_reason(span.get("attributes", []))
    if attribute_reason is not None:
        return attribute_reason
    if span.get("events"):
        return "content_policy_rejected"
    if span.get("links"):
        return "unreviewed_span_links"
    return None


def _parse_span(span: Any, resource_reason: str | None) -> _ParsedSpan | None:
    if not isinstance(span, dict):
        return None
    trace_id = span.get("traceId")
    span_id = span.get("spanId")
    if (
        not isinstance(trace_id, str)
        or TRACE_ID_RE.fullmatch(trace_id) is None
        or not isinstance(span_id, str)
        or SPAN_ID_RE.fullmatch(span_id) is None
    ):
        return None
    parent_span_id = span.get("parentSpanId")
    if parent_span_id in (None, "", "0" * 16):
        parent_span_id = None
    elif (
        not isinstance(parent_span_id, str)
        or SPAN_ID_RE.fullmatch(parent_span_id) is None
    ):
        return None
    status = span.get("status")
    status_code: Any = None
    if isinstance(status, dict):
        status_code = status.get("code")
    external_run_id, external_run_reason = _safe_external_run_id(
        span.get("attributes", [])
    )
    run_public_id, run_public_reason = _safe_run_public_id(
        span.get("attributes", [])
    )
    return _ParsedSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        external_run_id=external_run_id,
        run_public_id=run_public_id,
        started_at=_unix_nano(span.get("startTimeUnixNano")),
        ended_at=_unix_nano(span.get("endTimeUnixNano")),
        failed=status_code in (2, "2", "STATUS_CODE_ERROR", "ERROR"),
        policy_reason=(
            resource_reason
            or _span_policy_reason(span)
            or external_run_reason
            or run_public_reason
        ),
    )


def parse_generic_otlp_json(payload: Any) -> GenericOtlpParseResult:
    """Parse standard OTLP JSON into bounded, metadata-only trace candidates."""

    if not isinstance(payload, dict):
        raise GenericOtlpPayloadError("OTLP request must be a JSON object")
    resource_spans = payload.get("resourceSpans")
    if not isinstance(resource_spans, list):
        raise GenericOtlpPayloadError("resourceSpans must be an array")

    grouped: dict[str, list[_ParsedSpan]] = {}
    rejected_spans = 0
    total_spans = 0
    for resource_item in resource_spans:
        if not isinstance(resource_item, dict):
            raise GenericOtlpPayloadError("resourceSpans contains an invalid item")
        resource = resource_item.get("resource", {})
        resource_attributes: Any = []
        if isinstance(resource, dict):
            resource_attributes = resource.get("attributes", [])
        resource_reason = _attribute_policy_reason(resource_attributes)
        if _contains_canary(resource_item.get("schemaUrl")):
            resource_reason = "secret_canary_detected"
        scope_spans = resource_item.get("scopeSpans")
        if not isinstance(scope_spans, list):
            raise GenericOtlpPayloadError("scopeSpans must be an array")
        for scope_item in scope_spans:
            if not isinstance(scope_item, dict):
                raise GenericOtlpPayloadError("scopeSpans contains an invalid item")
            spans = scope_item.get("spans")
            if not isinstance(spans, list):
                raise GenericOtlpPayloadError("spans must be an array")
            for raw_span in spans:
                total_spans += 1
                if total_spans > MAX_OTLP_SPANS:
                    raise GenericOtlpPayloadError(
                        f"OTLP request exceeds {MAX_OTLP_SPANS} spans"
                    )
                parsed = _parse_span(raw_span, resource_reason)
                if parsed is None:
                    rejected_spans += 1
                    continue
                grouped.setdefault(parsed.trace_id, []).append(parsed)

    candidates: list[GenericTraceCandidate] = []
    for trace_id, spans in grouped.items():
        roots = [span for span in spans if span.parent_span_id is None]
        external_run_ids = {
            span.external_run_id
            for span in spans
            if span.external_run_id is not None
        }
        run_public_ids = {
            span.run_public_id
            for span in spans
            if span.run_public_id is not None
        }
        policy_reasons = {
            span.policy_reason
            for span in spans
            if span.policy_reason is not None
        }
        reason: str | None = None
        if "secret_canary_detected" in policy_reasons:
            reason = "secret_canary_detected"
        elif policy_reasons:
            reason = sorted(policy_reasons)[0]
        elif len(roots) > 1:
            reason = "ambiguous_root_spans"
        elif len(external_run_ids) > 1:
            reason = "conflicting_external_run_ids"
        elif len(run_public_ids) > 1:
            reason = "conflicting_run_public_ids"

        root = roots[0] if len(roots) == 1 else None
        observed_starts = [
            span.started_at for span in spans if span.started_at is not None
        ]
        observed_ends = [
            span.ended_at for span in spans if span.ended_at is not None
        ]
        candidates.append(
            GenericTraceCandidate(
                trace_id=trace_id,
                span_count=len(spans),
                candidate_root_count=len(roots),
                root_span_id=root.span_id if root is not None else None,
                external_run_id=(
                    next(iter(external_run_ids))
                    if len(external_run_ids) == 1
                    else None
                ),
                run_public_id=(
                    next(iter(run_public_ids))
                    if len(run_public_ids) == 1
                    else None
                ),
                started_at=root.started_at if root is not None else None,
                ended_at=root.ended_at if root is not None else None,
                first_observed_at=(
                    min(observed_starts) if observed_starts else None
                ),
                last_observed_at=(
                    max(observed_ends) if observed_ends else None
                ),
                failed=root.failed if root is not None else False,
                quarantine_reason=reason,
            )
        )
    reason = "invalid_trace_identity" if rejected_spans else None
    return GenericOtlpParseResult(
        candidates=candidates,
        rejected_spans=rejected_spans,
        rejection_reason=reason,
    )


def _projection_public_id() -> str:
    return f"gtp_{uuid.uuid4().hex}"


def _deterministic_external_run_id(
    *,
    runtime_id: int,
    trace_id: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{runtime_id}:{trace_id}:"
            f"{GENERIC_OTLP_NORMALIZER_VERSION}"
        ).encode("utf-8")
    ).hexdigest()
    return f"otel-{digest[:40]}"


def _earlier(
    current: datetime | None,
    candidate: datetime | None,
) -> datetime | None:
    if current is None:
        return candidate
    if candidate is None:
        return current
    return min(current, candidate)


def _later(
    current: datetime | None,
    candidate: datetime | None,
) -> datetime | None:
    if current is None:
        return candidate
    if candidate is None:
        return current
    return max(current, candidate)


async def _find_signal_runs(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    candidate: GenericTraceCandidate,
) -> tuple[AgentRun | None, str | None]:
    signals: list[AgentRun] = []
    reason: str | None = None

    if candidate.external_run_id is not None:
        external_run = (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.namespace_id == identity.namespace_id,
                    AgentRun.runtime_id == identity.runtime_id,
                    AgentRun.external_run_id == candidate.external_run_id,
                )
            )
        ).scalar_one_or_none()
        if external_run is not None:
            signals.append(external_run)

    if candidate.run_public_id is not None:
        public_run = (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.public_id == candidate.run_public_id
                )
            )
        ).scalar_one_or_none()
        if (
            public_run is None
            or public_run.namespace_id != identity.namespace_id
            or public_run.runtime_id != identity.runtime_id
        ):
            reason = "run_public_id_not_visible"
        else:
            signals.append(public_run)

    trace_run = (
        await db.execute(
            select(AgentRun).where(
                AgentRun.namespace_id == identity.namespace_id,
                AgentRun.otel_trace_id == candidate.trace_id,
            )
        )
    ).scalar_one_or_none()
    if trace_run is not None:
        if trace_run.runtime_id != identity.runtime_id:
            reason = "trace_reused_across_runtime"
        else:
            signals.append(trace_run)
            if (
                candidate.external_run_id is not None
                and trace_run.external_run_id
                != candidate.external_run_id
            ):
                reason = "trace_run_identity_conflict"
            if (
                candidate.run_public_id is not None
                and trace_run.public_id != candidate.run_public_id
            ):
                reason = "trace_run_identity_conflict"

    by_id = {run.id: run for run in signals}
    if len(by_id) > 1:
        reason = "mapping_signal_conflict"
    run = next(iter(by_id.values())) if len(by_id) == 1 else None
    if run is not None:
        if (
            run.otel_trace_id is not None
            and run.otel_trace_id != candidate.trace_id
        ):
            reason = "run_trace_identity_conflict"
        if (
            run.root_span_id is not None
            and candidate.root_span_id is not None
            and run.root_span_id != candidate.root_span_id
        ):
            reason = "root_span_identity_conflict"
    return run, reason


async def _find_reference_conflict(
    db: AsyncSession,
    *,
    sink: TelemetrySink,
    candidate: GenericTraceCandidate,
    run: AgentRun | None,
) -> tuple[TraceBackendRef | None, str | None]:
    trace_reference = (
        await db.execute(
            select(TraceBackendRef).where(
                TraceBackendRef.telemetry_sink_id == sink.id,
                TraceBackendRef.external_trace_id == candidate.trace_id,
            )
        )
    ).scalar_one_or_none()
    if (
        trace_reference is not None
        and run is not None
        and trace_reference.agent_run_id != run.id
    ):
        return trace_reference, "trace_reference_conflict"
    if run is None:
        return trace_reference, None
    run_reference = (
        await db.execute(
            select(TraceBackendRef).where(
                TraceBackendRef.agent_run_id == run.id,
                TraceBackendRef.telemetry_sink_id == sink.id,
            )
        )
    ).scalar_one_or_none()
    if (
        run_reference is not None
        and run_reference.external_trace_id != candidate.trace_id
    ):
        return trace_reference, "run_reference_conflict"
    return trace_reference or run_reference, None


async def _create_bridge_run(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    candidate: GenericTraceCandidate,
) -> AgentRun | None:
    if (
        candidate.candidate_root_count != 1
        or candidate.started_at is None
        or candidate.ended_at is None
        or candidate.ended_at < candidate.started_at
    ):
        return None
    external_run_id = candidate.external_run_id or _deterministic_external_run_id(
        runtime_id=identity.runtime_id,
        trace_id=candidate.trace_id,
    )
    start_key = f"otel-start:{candidate.trace_id}"
    run = await start_run(
        db,
        namespace_id=identity.namespace_id,
        runtime_id=identity.runtime_id,
        idempotency_key=start_key,
        envelope=RunStartEnvelope(
            external_run_id=external_run_id,
            otel_trace_id=candidate.trace_id,
            root_span_id=candidate.root_span_id,
            source_schema=GENERIC_OTLP_SOURCE_SCHEMA,
            source_schema_version=GENERIC_OTLP_SOURCE_SCHEMA_VERSION,
            started_at=candidate.started_at,
            content_capture_mode=ContentCaptureMode.METADATA_ONLY,
        ),
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.COLLECTOR,
        normalizer_version=GENERIC_OTLP_NORMALIZER_VERSION,
        tenant_scope_validated=True,
    )
    if run.status == AgentRunStatus.STARTED:
        run = await complete_run(
            db,
            run.public_id,
            idempotency_key=f"otel-complete:{candidate.trace_id}",
            envelope=RunCompleteEnvelope(
                ended_at=candidate.ended_at,
                status=(
                    AgentRunStatus.FAILED
                    if candidate.failed
                    else AgentRunStatus.SUCCEEDED
                ),
                otel_trace_id=candidate.trace_id,
                root_span_id=candidate.root_span_id,
            ),
            namespace_id=identity.namespace_id,
            runtime_id=identity.runtime_id,
        )
    return run


def _new_projection(
    *,
    identity: ReporterExecutionIdentity,
    sink: TelemetrySink,
    candidate: GenericTraceCandidate,
) -> GenericTraceProjection:
    return GenericTraceProjection(
        public_id=_projection_public_id(),
        namespace_id=identity.namespace_id,
        runtime_id=identity.runtime_id,
        telemetry_sink_id=sink.id,
        external_trace_id=candidate.trace_id,
        root_span_id=candidate.root_span_id,
        external_run_id=candidate.external_run_id,
        status=GenericTraceProjectionStatus.UNMATCHED,
        reason_code="root_span_not_observed",
        source_schema=GENERIC_OTLP_SOURCE_SCHEMA,
        source_schema_version=GENERIC_OTLP_SOURCE_SCHEMA_VERSION,
        normalizer_version=GENERIC_OTLP_NORMALIZER_VERSION,
        candidate_root_count=candidate.candidate_root_count,
        observed_span_count=candidate.span_count,
        first_observed_at=candidate.first_observed_at,
        last_observed_at=candidate.last_observed_at,
        content_capture_mode=ContentCaptureMode.METADATA_ONLY.value,
    )


async def reconcile_generic_trace_candidate(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    sink: TelemetrySink,
    candidate: GenericTraceCandidate,
) -> GenericTraceProjection:
    if (
        sink.namespace_id != identity.namespace_id
        or sink.status != TelemetrySinkStatus.ACTIVE
    ):
        raise GenericOtlpSinkError("TelemetrySink is not active for this Namespace")

    projection = (
        await db.execute(
            select(GenericTraceProjection)
            .where(
                GenericTraceProjection.namespace_id
                == identity.namespace_id,
                GenericTraceProjection.telemetry_sink_id == sink.id,
                GenericTraceProjection.external_trace_id
                == candidate.trace_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    created = projection is None
    previous_status = projection.status if projection is not None else None
    previous_reason = projection.reason_code if projection is not None else None
    if projection is None:
        projection = _new_projection(
            identity=identity,
            sink=sink,
            candidate=candidate,
        )
        db.add(projection)
    elif projection.runtime_id != identity.runtime_id:
        candidate.quarantine_reason = "trace_reused_across_runtime"

    projection.candidate_root_count = max(
        projection.candidate_root_count,
        candidate.candidate_root_count,
    )
    projection.observed_span_count = max(
        projection.observed_span_count,
        candidate.span_count,
    )
    projection.first_observed_at = _earlier(
        projection.first_observed_at,
        candidate.first_observed_at,
    )
    projection.last_observed_at = _later(
        projection.last_observed_at,
        candidate.last_observed_at,
    )

    if (
        projection.status == GenericTraceProjectionStatus.QUARANTINED
        and candidate.quarantine_reason is None
    ):
        await db.flush()
        return projection

    run, signal_reason = await _find_signal_runs(
        db,
        identity=identity,
        candidate=candidate,
    )
    if projection.agent_run_id is not None:
        prior_run = await db.get(AgentRun, projection.agent_run_id)
        if run is not None and run.id != projection.agent_run_id:
            signal_reason = "mapping_signal_conflict"
        elif run is None:
            run = prior_run
        if (
            prior_run is not None
            and candidate.external_run_id is not None
            and prior_run.external_run_id != candidate.external_run_id
        ):
            signal_reason = "trace_run_identity_conflict"

    reference, reference_reason = await _find_reference_conflict(
        db,
        sink=sink,
        candidate=candidate,
        run=run,
    )
    if run is None and reference is not None:
        referenced_run = await db.get(AgentRun, reference.agent_run_id)
        if (
            referenced_run is None
            or referenced_run.namespace_id != identity.namespace_id
            or referenced_run.runtime_id != identity.runtime_id
        ):
            reference_reason = "trace_reference_runtime_conflict"
        else:
            run = referenced_run
            if (
                candidate.external_run_id is not None
                and referenced_run.external_run_id
                != candidate.external_run_id
            ):
                reference_reason = "trace_run_identity_conflict"
            if (
                candidate.run_public_id is not None
                and referenced_run.public_id != candidate.run_public_id
            ):
                reference_reason = "trace_run_identity_conflict"
            if (
                candidate.root_span_id is not None
                and referenced_run.root_span_id is not None
                and referenced_run.root_span_id
                != candidate.root_span_id
            ):
                reference_reason = "root_span_identity_conflict"
    quarantine_reason = (
        candidate.quarantine_reason
        or signal_reason
        or reference_reason
    )
    if quarantine_reason is not None:
        projection.status = GenericTraceProjectionStatus.QUARANTINED
        projection.reason_code = quarantine_reason
        if quarantine_reason in {
            "secret_canary_detected",
            "content_policy_rejected",
        }:
            projection.external_run_id = None
            projection.root_span_id = None
        await db.flush()
        if (
            created
            or previous_status
            != GenericTraceProjectionStatus.QUARANTINED
            or previous_reason != quarantine_reason
        ):
            await audit(
                db,
                username="system:generic-otlp",
                action="generic_trace_projection.quarantined",
                resource_type="generic_trace_projection",
                resource_id=projection.id,
                namespace_id=identity.namespace_id,
                details={
                    "public_id": projection.public_id,
                    "reason_code": quarantine_reason,
                    "runtime_id": identity.runtime_id,
                },
            )
        return projection

    if run is None:
        run = await _create_bridge_run(
            db,
            identity=identity,
            candidate=candidate,
        )
    if run is None:
        projection.status = GenericTraceProjectionStatus.UNMATCHED
        projection.reason_code = (
            "invalid_root_time"
            if candidate.candidate_root_count == 1
            else "root_span_not_observed"
        )
        await db.flush()
        return projection

    reference, reference_reason = await _find_reference_conflict(
        db,
        sink=sink,
        candidate=candidate,
        run=run,
    )
    if reference_reason is not None:
        projection.status = GenericTraceProjectionStatus.QUARANTINED
        projection.reason_code = reference_reason
        await db.flush()
        return projection
    if reference is None:
        reference = TraceBackendRef(
            namespace_id=identity.namespace_id,
            agent_run_id=run.id,
            telemetry_sink_id=sink.id,
            external_trace_id=candidate.trace_id,
            status=TraceBackendRefStatus.PENDING,
        )
        db.add(reference)

    projection.agent_run_id = run.id
    projection.external_run_id = run.external_run_id
    projection.root_span_id = candidate.root_span_id or run.root_span_id
    projection.status = GenericTraceProjectionStatus.MAPPED
    projection.reason_code = None
    await db.flush()
    if (
        created
        or previous_status != GenericTraceProjectionStatus.MAPPED
    ):
        await audit(
            db,
            username="system:generic-otlp",
            action="generic_trace_projection.mapped",
            resource_type="generic_trace_projection",
            resource_id=projection.id,
            namespace_id=identity.namespace_id,
            details={
                "public_id": projection.public_id,
                "run_public_id": run.public_id,
                "runtime_id": identity.runtime_id,
                "sink_public_id": sink.public_id,
            },
        )
    return projection


async def ingest_generic_otlp_json(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    sink: TelemetrySink,
    payload: Any,
) -> GenericOtlpReconcileResult:
    parsed = parse_generic_otlp_json(payload)
    result = GenericOtlpReconcileResult(
        rejected_spans=parsed.rejected_spans,
        rejection_reason=parsed.rejection_reason,
    )
    for candidate in parsed.candidates:
        projection = await reconcile_generic_trace_candidate(
            db,
            identity=identity,
            sink=sink,
            candidate=candidate,
        )
        result.projections.append(projection)
        if projection.status == GenericTraceProjectionStatus.QUARANTINED:
            result.rejected_spans += candidate.span_count
            if result.rejection_reason is None:
                result.rejection_reason = projection.reason_code
    return result
