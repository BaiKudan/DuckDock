"""Fail-closed compatibility gate for DuckDock's Langfuse v4 boundary.

The gate exercises the real server and the exact production adapters:
health/version, SDK/auth, Clinic observations including content-free curation
candidate discovery and the v4 raw IO contract, Trace2Dataset source linkage,
a pinned two-item Dataset experiment,
Experiments API result materialization, manifest generation, and direct OTLP
v4 ingestion followed by an Observations API v2 lookup. It also creates or
reuses a fixed Annotation Queue contract fixture and proves idempotent
Observation dispatch plus PENDING/COMPLETED reconciliation.
It also proves the annotation-driven Promotion boundary against Scores API v3:
exact queue/config/source/subject filtering, typed quality evaluation, and
metadata-only diversity evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.evaluation.langfuse import (  # noqa: E402
    LangfuseAnnotationQueueAdapter,
    build_langfuse_experiment_runner,
)
from app.services.evaluation_ports import (  # noqa: E402
    EvaluationRunRequest,
    PromotionEvidenceSource,
    PromotionQualityRule,
)
from app.services.langfuse_compatibility import (  # noqa: E402
    LANGFUSE_COMPATIBILITY_PROFILE,
    LANGFUSE_EXPERIMENT_FIELDS,
    LANGFUSE_OBSERVATIONS_FIELDS,
    LANGFUSE_OTLP_INGESTION_VERSION,
    LANGFUSE_RESULT_SCHEMA_NAME,
    LANGFUSE_RESULT_SCHEMA_VERSION,
    LANGFUSE_SDK_BASELINE_VERSION,
    LANGFUSE_SERVER_BASELINE_VERSION,
    require_langfuse_sdk_compatibility,
)


FIXTURE_DATASET_NAME = "duckdock.compatibility.langfuse-v4"
FIXTURE_ANNOTATION_QUEUE_NAME = "duckdock.compatibility.annotation-v4"
FIXTURE_ANNOTATION_SCORE_NAME = "duckdock_compat_human_quality"
FIXTURE_PROMOTION_QUEUE_NAME = "duckdock.compatibility.promotion-v4"
FIXTURE_PROMOTION_SCORE_NAME = "duckdock_compat_promotion_quality"
EXACT_MATCH_SCORE = "exact_match"


class CompatibilityFailure(RuntimeError):
    pass


class _EchoTarget:
    def execute(
        self,
        *,
        target_type: str,
        target_ref: str,
        target_digest: str,
        item_input: Any,
        item_metadata: dict[str, Any] | None,
    ) -> Any:
        return item_input


def _wait_for(
    operation: Callable[[], Any | None],
    *,
    timeout_seconds: int,
    label: str,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = operation()
        except Exception as exc:  # provider materialization is asynchronous
            last_error = exc
        else:
            if result is not None:
                return result
        time.sleep(1)
    detail = (
        f"; last_error={type(last_error).__name__}: {last_error}"
        if last_error is not None
        else ""
    )
    raise CompatibilityFailure(f"Timed out waiting for {label}{detail}")


def _verify_health(
    *,
    host: str,
    expected_server_version: str,
    timeout_seconds: float,
) -> None:
    response = httpx.get(
        f"{host.rstrip('/')}/api/public/health",
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "OK":
        raise CompatibilityFailure(
            f"Langfuse health is not OK: {payload!r}"
        )
    actual_version = payload.get("version")
    if actual_version != expected_server_version:
        raise CompatibilityFailure(
            "Langfuse server version has not passed the DuckDock "
            f"compatibility gate: expected={expected_server_version}, "
            f"actual={actual_version}"
        )
    print(f"[ok] server health/version {actual_version}")


def _build_client(
    *,
    host: str,
    public_key: str,
    secret_key: str,
    environment: str,
    timeout_seconds: float,
):
    from langfuse import Langfuse

    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
        environment=environment,
        timeout=timeout_seconds,
    )
    if not client.auth_check():
        raise CompatibilityFailure("Langfuse auth_check returned false")
    print("[ok] SDK client/auth")
    return client


def _verify_clinic_round_trip(
    *,
    client,
    poll_timeout_seconds: int,
) -> tuple[str, str]:
    started_at = datetime.now(timezone.utc)
    marker = uuid.uuid4().hex
    with client.start_as_current_observation(
        name="duckdock.compatibility.clinic",
        as_type="span",
        metadata={
            "duckdock.compatibility_profile": (
                LANGFUSE_COMPATIBILITY_PROFILE
            ),
            "duckdock.compatibility_marker": marker,
        },
    ) as span:
        trace_id = getattr(span, "trace_id", None)
        with span.start_as_current_observation(
            name="duckdock.compatibility.generation",
            as_type="generation",
            model="compatibility-fixture",
            input={"fixture": True},
        ) as generation:
            observation_id = getattr(generation, "id", None)
            generation.update(output={"ok": True})
    client.flush()
    if not isinstance(trace_id, str) or len(trace_id) != 32:
        raise CompatibilityFailure("Clinic span returned no OTEL trace id")
    if (
        not isinstance(observation_id, str)
        or len(observation_id) != 16
    ):
        raise CompatibilityFailure(
            "Clinic generation returned no OTEL observation id"
        )

    def lookup():
        response = client.api.observations.get_many(
            fields=LANGFUSE_OBSERVATIONS_FIELDS,
            trace_id=trace_id,
            limit=10,
            from_start_time=started_at - timedelta(minutes=1),
            to_start_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        return next(
            (
                item
                for item in response.data
                if item.trace_id == trace_id
                and (item.metadata or {}).get(
                    "duckdock.compatibility_marker"
                )
                == marker
            ),
            None,
        )

    _wait_for(
        lookup,
        timeout_seconds=poll_timeout_seconds,
        label="Clinic observation",
    )

    def curation_candidate_lookup():
        response = client.api.observations.get_many(
            fields="core,basic,time",
            name="duckdock.compatibility.clinic",
            is_root_observation=True,
            limit=10,
            from_start_time=started_at - timedelta(minutes=1),
            to_start_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        return next(
            (
                item
                for item in response.data
                if item.trace_id == trace_id
                and item.input is None
                and item.output is None
            ),
            None,
        )

    _wait_for(
        curation_candidate_lookup,
        timeout_seconds=poll_timeout_seconds,
        label="content-free curation candidate query",
    )

    def io_lookup():
        response = client.api.observations.get_many(
            fields="core,basic,time,io",
            trace_id=trace_id,
            limit=10,
            from_start_time=started_at - timedelta(minutes=1),
            to_start_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        for item in response.data:
            if (
                item.id != observation_id
                or not isinstance(item.input, str)
                or not isinstance(item.output, str)
            ):
                continue
            try:
                input_value = json.loads(item.input)
                output_value = json.loads(item.output)
            except json.JSONDecodeError:
                continue
            if input_value == {"fixture": True} and output_value == {
                "ok": True
            }:
                return item
        return None

    _wait_for(
        io_lookup,
        timeout_seconds=poll_timeout_seconds,
        label="Observation API v2 raw IO",
    )
    trace_url = client.get_trace_url(trace_id=trace_id)
    if not trace_url:
        raise CompatibilityFailure("Langfuse returned no Clinic trace URL")
    print(
        "[ok] Clinic span/generation + content-free candidate query "
        "+ Observations API v2 raw IO"
    )
    return trace_id, observation_id


def _prepare_dataset(
    client,
    *,
    source_trace_id: str,
    source_observation_id: str,
) -> tuple[str, datetime]:
    dataset = client.create_dataset(
        name=FIXTURE_DATASET_NAME,
        description="DuckDock Langfuse v4 compatibility fixture",
        metadata={
            "duckdock.compatibility_profile": (
                LANGFUSE_COMPATIBILITY_PROFILE
            ),
            "duckdock.fixture": True,
        },
    )
    for index, value in enumerate(("alpha", "beta"), start=1):
        client.create_dataset_item(
            dataset_name=FIXTURE_DATASET_NAME,
            id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{FIXTURE_DATASET_NAME}:{index}",
                )
            ),
            input={"value": value},
            expected_output={"value": value},
            metadata={"fixture": index},
            source_trace_id=source_trace_id if index == 1 else None,
            source_observation_id=(
                source_observation_id if index == 1 else None
            ),
        )
    hydrated = client.get_dataset(FIXTURE_DATASET_NAME)
    if len(hydrated.items) != 2:
        raise CompatibilityFailure(
            "Compatibility Dataset must contain exactly two items; "
            f"actual={len(hydrated.items)}"
        )
    source_item = next(
        (
            item
            for item in hydrated.items
            if item.source_trace_id == source_trace_id
            and item.source_observation_id == source_observation_id
        ),
        None,
    )
    if source_item is None:
        raise CompatibilityFailure(
            "Trace2Dataset source linkage was not preserved"
        )
    pinned_at = (
        max(item.updated_at for item in hydrated.items)
        + timedelta(milliseconds=1)
    )
    pinned = client.get_dataset(
        FIXTURE_DATASET_NAME,
        version=pinned_at,
    )
    if len(pinned.items) != 2 or pinned.version != pinned_at:
        raise CompatibilityFailure("Pinned Dataset version lookup failed")
    print(
        "[ok] Dataset create/upsert/source-link/pin "
        f"id={dataset.id}"
    )
    return dataset.id, pinned_at


def _all_score_configs(client) -> list[Any]:
    values: list[Any] = []
    page = 1
    while True:
        response = client.api.score_configs.get(page=page, limit=100)
        values.extend(response.data)
        if page >= response.meta.total_pages:
            return values
        page += 1


def _all_annotation_queues(client) -> list[Any]:
    values: list[Any] = []
    page = 1
    while True:
        response = client.api.annotation_queues.list_queues(
            page=page,
            limit=100,
        )
        values.extend(response.data)
        if page >= response.meta.total_pages:
            return values
        page += 1


def _prepare_annotation_queue(client):
    from langfuse.api.commons.types.score_config_data_type import (
        ScoreConfigDataType,
    )

    score_config = next(
        (
            value
            for value in _all_score_configs(client)
            if value.name == FIXTURE_ANNOTATION_SCORE_NAME
            and not value.is_archived
        ),
        None,
    )
    if score_config is None:
        score_config = client.api.score_configs.create(
            name=FIXTURE_ANNOTATION_SCORE_NAME,
            data_type=ScoreConfigDataType.TEXT,
            description=(
                "DuckDock Langfuse v4 Annotation Queue compatibility fixture"
            ),
        )

    queue = next(
        (
            value
            for value in _all_annotation_queues(client)
            if value.name == FIXTURE_ANNOTATION_QUEUE_NAME
        ),
        None,
    )
    if queue is None:
        queue = client.api.annotation_queues.create_queue(
            name=FIXTURE_ANNOTATION_QUEUE_NAME,
            score_config_ids=[score_config.id],
            description=(
                "DuckDock Langfuse v4 idempotent dispatch contract fixture"
            ),
        )
    if tuple(queue.score_config_ids) != (score_config.id,):
        raise CompatibilityFailure(
            "Annotation Queue fixture score config binding changed"
        )
    return queue


def _prepare_promotion_queue(client):
    from langfuse.api.commons.types.score_config_data_type import (
        ScoreConfigDataType,
    )

    score_config = next(
        (
            value
            for value in _all_score_configs(client)
            if value.name == FIXTURE_PROMOTION_SCORE_NAME
            and not value.is_archived
        ),
        None,
    )
    if score_config is None:
        score_config = client.api.score_configs.create(
            name=FIXTURE_PROMOTION_SCORE_NAME,
            data_type=ScoreConfigDataType.NUMERIC,
            description=(
                "DuckDock Langfuse v4 Promotion compatibility fixture"
            ),
        )
    if getattr(score_config.data_type, "value", score_config.data_type) != (
        "NUMERIC"
    ):
        raise CompatibilityFailure(
            "Promotion fixture score config data type changed"
        )

    queue = next(
        (
            value
            for value in _all_annotation_queues(client)
            if value.name == FIXTURE_PROMOTION_QUEUE_NAME
        ),
        None,
    )
    if queue is None:
        queue = client.api.annotation_queues.create_queue(
            name=FIXTURE_PROMOTION_QUEUE_NAME,
            score_config_ids=[score_config.id],
            description=(
                "DuckDock Langfuse v4 Promotion evidence contract fixture"
            ),
        )
    if tuple(queue.score_config_ids) != (score_config.id,):
        raise CompatibilityFailure(
            "Promotion fixture queue score config binding changed"
        )
    return queue, score_config


def _verify_annotation_queue_contract(
    *,
    client,
    source_observation_id: str,
) -> None:
    from langfuse.api.annotation_queues.types.annotation_queue_status import (
        AnnotationQueueStatus,
    )

    queue = _prepare_annotation_queue(client)
    adapter = LangfuseAnnotationQueueAdapter(client=client)
    created_item_ref: str | None = None
    try:
        first = asyncio.run(
            adapter.ensure_observations(
                queue_ref=queue.id,
                observation_refs=(source_observation_id,),
            )
        )
        second = asyncio.run(
            adapter.ensure_observations(
                queue_ref=queue.id,
                observation_refs=(source_observation_id,),
            )
        )
        if (
            len(first) != 1
            or len(second) != 1
            or first[0].provider_queue_item_ref
            != second[0].provider_queue_item_ref
        ):
            raise CompatibilityFailure(
                "Annotation Queue dispatch is not idempotent"
            )
        created_item_ref = first[0].provider_queue_item_ref
        matching = [
            value
            for value in client.api.annotation_queues.list_queue_items(
                queue.id,
                page=1,
                limit=100,
            ).data
            if value.object_id == source_observation_id
        ]
        if len(matching) != 1:
            raise CompatibilityFailure(
                "Annotation Queue contains duplicate Observation items"
            )
        client.api.annotation_queues.update_queue_item(
            queue.id,
            created_item_ref,
            status=AnnotationQueueStatus.COMPLETED,
        )
        reconciled = asyncio.run(
            adapter.get_observation_items(
                queue_ref=queue.id,
                observation_refs=(source_observation_id,),
            )
        )
        if len(reconciled) != 1 or reconciled[0].status != "COMPLETED":
            raise CompatibilityFailure(
                "Annotation Queue completion did not reconcile"
            )
        print(
            "[ok] Annotation Queue bind/idempotent dispatch/"
            f"completion reconciliation id={queue.id}"
        )
    finally:
        if created_item_ref is not None:
            client.api.annotation_queues.delete_queue_item(
                queue.id,
                created_item_ref,
            )


def _verify_promotion_evidence_contract(
    *,
    client,
    source_trace_id: str,
    source_observation_id: str,
    poll_timeout_seconds: int,
) -> None:
    from langfuse.api.annotation_queues.types.annotation_queue_status import (
        AnnotationQueueStatus,
    )
    from langfuse.api.commons.types.score_data_type import ScoreDataType
    from langfuse.api.scores.types.create_score_source import (
        CreateScoreSource,
    )

    queue, score_config = _prepare_promotion_queue(client)
    adapter = LangfuseAnnotationQueueAdapter(client=client)
    created_item_ref: str | None = None
    score_id = f"duckdock-compat-promotion-{uuid.uuid4().hex}"
    try:
        dispatched = asyncio.run(
            adapter.ensure_observations(
                queue_ref=queue.id,
                observation_refs=(source_observation_id,),
            )
        )
        if len(dispatched) != 1:
            raise CompatibilityFailure(
                "Promotion fixture Observation dispatch failed"
            )
        created_item_ref = dispatched[0].provider_queue_item_ref
        client.api.annotation_queues.update_queue_item(
            queue.id,
            created_item_ref,
            status=AnnotationQueueStatus.COMPLETED,
        )
        client.api.scores.create(
            id=score_id,
            name=FIXTURE_PROMOTION_SCORE_NAME,
            value=0.9,
            trace_id=source_trace_id,
            observation_id=source_observation_id,
            queue_id=queue.id,
            data_type=ScoreDataType.NUMERIC,
            config_id=score_config.id,
            source=CreateScoreSource.ANNOTATION,
        )

        def lookup():
            evidence = asyncio.run(
                adapter.evaluate_promotion_evidence(
                    queue_ref=queue.id,
                    quality_rule=PromotionQualityRule(
                        score_config_id=score_config.id,
                        score_data_type="NUMERIC",
                        minimum_numeric_score=0.8,
                        accepted_values=(),
                    ),
                    diversity_dimension="OBSERVATION_NAME",
                    sources=(
                        PromotionEvidenceSource(
                            source_trace_ref=source_trace_id,
                            source_observation_ref=source_observation_id,
                        ),
                    ),
                )
            )
            if len(evidence) != 1 or not evidence[0].score_present:
                return None
            return evidence[0]

        evidence = _wait_for(
            lookup,
            timeout_seconds=poll_timeout_seconds,
            label="Scores API v3 Promotion evidence",
        )
        if (
            not evidence.quality_passed
            or not evidence.diversity_bucket_present
            or len(evidence.score_evidence_digest or "") != 64
            or len(evidence.diversity_bucket_digest or "") != 64
        ):
            raise CompatibilityFailure(
                "Promotion quality/diversity evidence is incomplete"
            )
        print(
            "[ok] Scores API v3 annotation Promotion quality/"
            f"metadata-only diversity id={score_id}"
        )
    finally:
        if created_item_ref is not None:
            client.api.annotation_queues.delete_queue_item(
                queue.id,
                created_item_ref,
            )


def _run_governed_experiment(
    *,
    client,
    provider_dataset_ref: str,
    dataset_version: datetime,
):
    runner = build_langfuse_experiment_runner(
        compatibility_profile=LANGFUSE_COMPATIBILITY_PROFILE,
        client=client,
        target_adapter=_EchoTarget(),
        max_concurrency=2,
    )
    marker = uuid.uuid4().hex
    request = EvaluationRunRequest(
        evaluation_public_id=f"compat_{marker}",
        experiment_public_id=f"compat_{marker[:16]}",
        experiment_name="Langfuse v4 compatibility",
        namespace_id=0,
        dataset_name=FIXTURE_DATASET_NAME,
        provider_dataset_ref=provider_dataset_ref,
        dataset_version=dataset_version,
        expected_item_count=2,
        target_type="COMPATIBILITY_FIXTURE",
        target_ref="duckdock://compatibility/echo",
        target_digest="0" * 64,
        evaluator_kind="RULE",
        evaluator_provider="CUSTOM",
        evaluator_implementation_ref="rule://exact_match",
        evaluator_config_digest="1" * 64,
    )
    summary = asyncio.run(runner.run(request=request))
    client.flush()
    manifest = summary.result_manifest
    if (
        summary.completeness != "COMPLETE"
        or summary.total_count != 2
        or summary.scored_count != 2
        or summary.passed_count != 2
        or summary.failed_count != 0
        or summary.error_count != 0
    ):
        raise CompatibilityFailure(
            f"Governed experiment result is incomplete: {summary!r}"
        )
    if (
        manifest.schema_name != LANGFUSE_RESULT_SCHEMA_NAME
        or manifest.schema_version != LANGFUSE_RESULT_SCHEMA_VERSION
        or len(manifest.content_digest) != 64
    ):
        raise CompatibilityFailure(
            f"Result manifest contract changed: {manifest!r}"
        )
    print(
        "[ok] governed two-item experiment + manifest "
        f"id={summary.provider_evaluation_ref}"
    )
    return summary


def _verify_experiment_api(
    *,
    client,
    provider_evaluation_ref: str,
    poll_timeout_seconds: int,
) -> None:
    started_after = datetime.now(timezone.utc) - timedelta(minutes=10)

    def lookup():
        response = client.api.experiments.list(
            from_start_time=started_after,
            fields=LANGFUSE_EXPERIMENT_FIELDS,
            limit=1,
            id=provider_evaluation_ref,
        )
        if not response.data or response.data[0].item_count != 2:
            return None
        item_response = client.api.experiments.list_items(
            from_start_time=started_after,
            fields=LANGFUSE_EXPERIMENT_FIELDS,
            limit=10,
            experiment_id=provider_evaluation_ref,
        )
        if len(item_response.data) != 2:
            return None
        for item in item_response.data:
            matching = [
                score
                for score in (item.scores or [])
                if score.name == EXACT_MATCH_SCORE
                and float(score.value) == 1.0
            ]
            if len(matching) != 1:
                return None
        return item_response.data

    _wait_for(
        lookup,
        timeout_seconds=poll_timeout_seconds,
        label="Experiments API scores",
    )
    print("[ok] Experiments API core/metadata/scores")


def _otlp_payload(*, trace_id: str, span_id: str) -> dict[str, Any]:
    started_at = time.time_ns()
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {
                                "stringValue": (
                                    "duckdock-langfuse-compatibility"
                                )
                            },
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "duckdock.compatibility",
                            "version": "1",
                        },
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "duckdock.compatibility.otlp",
                                "kind": 1,
                                "startTimeUnixNano": str(started_at),
                                "endTimeUnixNano": str(
                                    started_at + 1_000_000
                                ),
                                "attributes": [
                                    {
                                        "key": (
                                            "duckdock.compatibility_profile"
                                        ),
                                        "value": {
                                            "stringValue": (
                                                LANGFUSE_COMPATIBILITY_PROFILE
                                            )
                                        },
                                    }
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _verify_direct_otlp(
    *,
    client,
    host: str,
    public_key: str,
    secret_key: str,
    http_timeout_seconds: float,
    poll_timeout_seconds: int,
) -> None:
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    started_at = datetime.now(timezone.utc)
    response = httpx.post(
        f"{host.rstrip('/')}/api/public/otel/v1/traces",
        auth=httpx.BasicAuth(public_key, secret_key),
        headers={
            "Content-Type": "application/json",
            "x-langfuse-ingestion-version": (
                LANGFUSE_OTLP_INGESTION_VERSION
            ),
        },
        json=_otlp_payload(trace_id=trace_id, span_id=span_id),
        timeout=http_timeout_seconds,
    )
    response.raise_for_status()

    def lookup():
        observations = client.api.observations.get_many(
            fields=LANGFUSE_OBSERVATIONS_FIELDS,
            trace_id=trace_id,
            limit=5,
            from_start_time=started_at - timedelta(minutes=1),
            to_start_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        return next(
            (
                item
                for item in observations.data
                if item.trace_id == trace_id
            ),
            None,
        )

    _wait_for(
        lookup,
        timeout_seconds=poll_timeout_seconds,
        label="direct OTLP observation",
    )
    print("[ok] direct OTLP ingestion v4 + observation confirmation")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify DuckDock's Langfuse compatibility profile",
    )
    parser.add_argument(
        "--expected-server-version",
        default=LANGFUSE_SERVER_BASELINE_VERSION,
    )
    parser.add_argument(
        "--expected-sdk-version",
        default=LANGFUSE_SDK_BASELINE_VERSION,
    )
    parser.add_argument("--http-timeout", type=float, default=10)
    parser.add_argument("--poll-timeout", type=int, default=45)
    parser.add_argument(
        "--skip-otlp",
        action="store_true",
        help="Skip only when the candidate deployment cannot expose OTLP",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    load_dotenv(REPO_ROOT / ".env")
    host = os.environ.get(
        "LANGFUSE_BASE_URL",
        "http://127.0.0.1:3200",
    ).rstrip("/")
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    environment = os.environ.get(
        "LANGFUSE_ENVIRONMENT",
        "development",
    )
    if not public_key or not secret_key:
        raise CompatibilityFailure(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required"
        )

    sdk_version = require_langfuse_sdk_compatibility(
        expected_version=args.expected_sdk_version,
    )
    print(
        "[profile] "
        f"{LANGFUSE_COMPATIBILITY_PROFILE} "
        f"server={args.expected_server_version} sdk={sdk_version}"
    )
    _verify_health(
        host=host,
        expected_server_version=args.expected_server_version,
        timeout_seconds=args.http_timeout,
    )
    client = _build_client(
        host=host,
        public_key=public_key,
        secret_key=secret_key,
        environment=environment,
        timeout_seconds=args.http_timeout,
    )
    trace_id, observation_id = _verify_clinic_round_trip(
        client=client,
        poll_timeout_seconds=args.poll_timeout,
    )
    _verify_annotation_queue_contract(
        client=client,
        source_observation_id=observation_id,
    )
    _verify_promotion_evidence_contract(
        client=client,
        source_trace_id=trace_id,
        source_observation_id=observation_id,
        poll_timeout_seconds=args.poll_timeout,
    )
    dataset_id, dataset_version = _prepare_dataset(
        client,
        source_trace_id=trace_id,
        source_observation_id=observation_id,
    )
    summary = _run_governed_experiment(
        client=client,
        provider_dataset_ref=dataset_id,
        dataset_version=dataset_version,
    )
    _verify_experiment_api(
        client=client,
        provider_evaluation_ref=summary.provider_evaluation_ref,
        poll_timeout_seconds=args.poll_timeout,
    )
    if not args.skip_otlp:
        _verify_direct_otlp(
            client=client,
            host=host,
            public_key=public_key,
            secret_key=secret_key,
            http_timeout_seconds=args.http_timeout,
            poll_timeout_seconds=args.poll_timeout,
        )
    print(
        "[SUCCESS] Langfuse candidate passed the DuckDock "
        f"{LANGFUSE_COMPATIBILITY_PROFILE} gate"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[FAIL] {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
