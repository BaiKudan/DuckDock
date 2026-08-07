from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.adapters.evaluation.deepeval import DeepEvalAdapter
from app.services.evaluation_ports import (
    AnnotationQueueDescriptor,
    AnnotationQueueItemSnapshot,
    EphemeralEvaluationCase,
    EvaluationAdapterPort,
    EvaluationResultManifestSummary,
    EvaluationRunRequest,
    EvaluationRunSummary,
    EvaluationTargetPort,
    MetricSpec,
    PromotionEvidenceItem,
    PromotionEvidenceSource,
    PromotionQualityRule,
    SemanticClusteringSource,
    SemanticEmbeddingEvidence,
    TraceDatasetBatchItemReceipt,
    TraceDatasetBatchMaterializationReceipt,
    TraceDatasetBatchSource,
    TraceDatasetCandidate,
    TraceDatasetMaterializationReceipt,
)
from app.services.langfuse_compatibility import (
    LANGFUSE_COMPATIBILITY_PROFILE,
    LANGFUSE_RESULT_SCHEMA_NAME,
    LANGFUSE_RESULT_SCHEMA_VERSION,
    require_langfuse_sdk_compatibility,
)


class LangfuseExperimentRunnerError(RuntimeError):
    pass


class LangfuseTraceDatasetMaterializerError(RuntimeError):
    pass


class LangfuseAnnotationQueueAdapterError(RuntimeError):
    pass


class LangfuseSemanticEmbeddingAdapterError(RuntimeError):
    pass


_TRACE_DATASET_SCHEMA_NAME = "langfuse-trace-dataset-index"
_TRACE_DATASET_SCHEMA_VERSION = "v4"
_MAX_IO_BYTES = 1_048_576
_MAX_TRACE_OBSERVATIONS = 10_000
_MAX_CURATION_BATCH_ITEMS = 20
_MAX_ANNOTATION_QUEUES = 100
_MAX_ANNOTATION_QUEUE_ITEMS = 10_000
_MAX_SEMANTIC_ITEMS = 20
_MAX_EMBEDDING_RESPONSE_BYTES = 4 * 1024 * 1024


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _metric_spec(implementation_ref: str) -> MetricSpec:
    prefix = "deepeval://"
    if not implementation_ref.startswith(prefix):
        raise LangfuseExperimentRunnerError(
            "Evaluator implementation is not supported by this runner"
        )
    descriptor = implementation_ref[len(prefix):]
    metric_name, separator, threshold_text = descriptor.partition("@")
    if not metric_name:
        raise LangfuseExperimentRunnerError("Evaluator metric is missing")
    threshold = 0.5
    if separator:
        try:
            threshold = float(threshold_text)
        except ValueError as exc:
            raise LangfuseExperimentRunnerError(
                "Evaluator threshold is invalid"
            ) from exc
    if not 0 <= threshold <= 1:
        raise LangfuseExperimentRunnerError(
            "Evaluator threshold is outside [0, 1]"
        )
    return MetricSpec(name=metric_name, threshold=threshold)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _provider_io(value: str | None, *, field_name: str) -> Any:
    if value is None:
        raise LangfuseTraceDatasetMaterializerError(
            f"Selected observation has no {field_name}"
        )
    if len(value.encode("utf-8")) > _MAX_IO_BYTES:
        raise LangfuseTraceDatasetMaterializerError(
            f"Selected observation {field_name} exceeds 1 MiB"
        )
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _item_status(value: Any) -> str:
    status = getattr(value, "status", None)
    status_value = getattr(status, "value", status)
    return status_value if isinstance(status_value, str) else "UNKNOWN"


def _item_manifest_record(value: Any) -> dict[str, str | None]:
    item_id = getattr(value, "id", None)
    updated_at = getattr(value, "updated_at", None)
    if (
        not isinstance(item_id, str)
        or not item_id
        or not isinstance(updated_at, datetime)
    ):
        raise LangfuseTraceDatasetMaterializerError(
            "Langfuse Dataset item identity is incomplete"
        )
    source_trace_ref = getattr(value, "source_trace_id", None)
    source_observation_ref = getattr(value, "source_observation_id", None)
    return {
        "id": item_id,
        "status": _item_status(value),
        "updated_at": _utc_iso(updated_at),
        "source_trace_ref": (
            source_trace_ref
            if isinstance(source_trace_ref, str)
            else None
        ),
        "source_observation_ref": (
            source_observation_ref
            if isinstance(source_observation_ref, str)
            else None
        ),
    }


def _bounded_metadata_text(value: Any, *, fallback: str = "") -> str:
    candidate = value if isinstance(value, str) else fallback
    return "".join(
        character for character in candidate.strip()[:200]
        if character.isprintable()
    )


def _required_provider_text(
    value: Any,
    *,
    field_name: str,
    max_length: int = 255,
) -> str:
    if not isinstance(value, str):
        raise LangfuseAnnotationQueueAdapterError(
            f"Langfuse {field_name} is missing"
        )
    candidate = "".join(
        character
        for character in value.strip()[:max_length]
        if character.isprintable()
    )
    if not candidate:
        raise LangfuseAnnotationQueueAdapterError(
            f"Langfuse {field_name} is missing"
        )
    return candidate


def _required_provider_datetime(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise LangfuseAnnotationQueueAdapterError(
            f"Langfuse {field_name} is missing"
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class LangfuseAnnotationQueueAdapter:
    """Public-API adapter with content-free reconciliation before creation."""

    def __init__(self, *, client: Any) -> None:
        self._client = client

    @staticmethod
    def _queue_descriptor(value: Any) -> AnnotationQueueDescriptor:
        raw_score_ids = getattr(value, "score_config_ids", ())
        if not isinstance(raw_score_ids, (list, tuple)):
            raise LangfuseAnnotationQueueAdapterError(
                "Langfuse queue score config identity is malformed"
            )
        score_ids = tuple(
            _required_provider_text(
                score_id,
                field_name="score config identity",
            )
            for score_id in raw_score_ids
        )
        if not score_ids:
            raise LangfuseAnnotationQueueAdapterError(
                "Langfuse annotation queue has no score config"
            )
        description_value = getattr(value, "description", None)
        return AnnotationQueueDescriptor(
            provider_queue_ref=_required_provider_text(
                getattr(value, "id", None),
                field_name="queue identity",
            ),
            name=_required_provider_text(
                getattr(value, "name", None),
                field_name="queue name",
                max_length=200,
            ),
            description=(
                _bounded_metadata_text(description_value)
                if isinstance(description_value, str)
                else None
            ),
            score_config_ids=score_ids,
            created_at=_required_provider_datetime(
                getattr(value, "created_at", None),
                field_name="queue creation timestamp",
            ),
            updated_at=_required_provider_datetime(
                getattr(value, "updated_at", None),
                field_name="queue update timestamp",
            ),
        )

    @staticmethod
    def _item_snapshot(value: Any) -> AnnotationQueueItemSnapshot:
        object_type = getattr(value, "object_type", None)
        object_type_value = getattr(object_type, "value", object_type)
        if object_type_value != "OBSERVATION":
            raise LangfuseAnnotationQueueAdapterError(
                "Langfuse queue item is not an Observation"
            )
        status = getattr(value, "status", None)
        status_value = getattr(status, "value", status)
        if status_value not in ("PENDING", "COMPLETED"):
            raise LangfuseAnnotationQueueAdapterError(
                "Langfuse queue item status is unsupported"
            )
        completed_at_value = getattr(value, "completed_at", None)
        return AnnotationQueueItemSnapshot(
            provider_queue_item_ref=_required_provider_text(
                getattr(value, "id", None),
                field_name="queue item identity",
            ),
            source_observation_ref=_required_provider_text(
                getattr(value, "object_id", None),
                field_name="queue item object identity",
                max_length=64,
            ),
            status=status_value,
            created_at=_required_provider_datetime(
                getattr(value, "created_at", None),
                field_name="queue item creation timestamp",
            ),
            updated_at=_required_provider_datetime(
                getattr(value, "updated_at", None),
                field_name="queue item update timestamp",
            ),
            completed_at=(
                _required_provider_datetime(
                    completed_at_value,
                    field_name="queue item completion timestamp",
                )
                if completed_at_value is not None
                else None
            ),
        )

    def _list_queues_sync(self, *, limit: int) -> list[AnnotationQueueDescriptor]:
        require_langfuse_sdk_compatibility()
        bounded_limit = max(1, min(limit, _MAX_ANNOTATION_QUEUES))
        try:
            response = self._client.api.annotation_queues.list_queues(
                page=1,
                limit=bounded_limit,
            )
            return [self._queue_descriptor(value) for value in response.data]
        except LangfuseAnnotationQueueAdapterError:
            raise
        except Exception as exc:
            raise LangfuseAnnotationQueueAdapterError(
                "Langfuse annotation queue listing failed"
            ) from exc

    def _get_queue_sync(self, *, queue_ref: str) -> AnnotationQueueDescriptor:
        require_langfuse_sdk_compatibility()
        try:
            value = self._client.api.annotation_queues.get_queue(queue_ref)
            descriptor = self._queue_descriptor(value)
            if descriptor.provider_queue_ref != queue_ref:
                raise LangfuseAnnotationQueueAdapterError(
                    "Langfuse returned another annotation queue"
                )
            return descriptor
        except LangfuseAnnotationQueueAdapterError:
            raise
        except Exception as exc:
            raise LangfuseAnnotationQueueAdapterError(
                "Langfuse annotation queue lookup failed"
            ) from exc

    def _all_queue_items(self, *, queue_ref: str) -> list[Any]:
        values: list[Any] = []
        page = 1
        while True:
            response = self._client.api.annotation_queues.list_queue_items(
                queue_ref,
                page=page,
                limit=100,
            )
            values.extend(response.data)
            if len(values) > _MAX_ANNOTATION_QUEUE_ITEMS:
                raise LangfuseAnnotationQueueAdapterError(
                    "Langfuse annotation queue exceeds 10000 items"
                )
            total_pages = int(getattr(response.meta, "total_pages", 1))
            if page >= total_pages:
                return values
            page += 1

    @staticmethod
    def _matching_item_map(
        values: Sequence[Any],
        *,
        observation_refs: set[str],
    ) -> dict[str, AnnotationQueueItemSnapshot]:
        matched: dict[str, AnnotationQueueItemSnapshot] = {}
        for value in values:
            object_type = getattr(value, "object_type", None)
            object_type_value = getattr(object_type, "value", object_type)
            object_id = getattr(value, "object_id", None)
            if (
                object_type_value != "OBSERVATION"
                or object_id not in observation_refs
            ):
                continue
            snapshot = LangfuseAnnotationQueueAdapter._item_snapshot(value)
            current = matched.get(snapshot.source_observation_ref)
            if current is None or (
                snapshot.created_at,
                snapshot.provider_queue_item_ref,
            ) > (current.created_at, current.provider_queue_item_ref):
                matched[snapshot.source_observation_ref] = snapshot
        return matched

    def _get_observation_items_sync(
        self,
        *,
        queue_ref: str,
        observation_refs: Sequence[str],
    ) -> list[AnnotationQueueItemSnapshot]:
        require_langfuse_sdk_compatibility()
        requested = tuple(dict.fromkeys(observation_refs))
        if not requested or len(requested) > 20:
            raise LangfuseAnnotationQueueAdapterError(
                "Annotation queue request must contain 1 to 20 Observations"
            )
        try:
            self._get_queue_sync(queue_ref=queue_ref)
            matched = self._matching_item_map(
                self._all_queue_items(queue_ref=queue_ref),
                observation_refs=set(requested),
            )
            return [matched[value] for value in requested if value in matched]
        except LangfuseAnnotationQueueAdapterError:
            raise
        except Exception as exc:
            raise LangfuseAnnotationQueueAdapterError(
                "Langfuse annotation queue reconciliation failed"
            ) from exc

    def _ensure_observations_sync(
        self,
        *,
        queue_ref: str,
        observation_refs: Sequence[str],
    ) -> list[AnnotationQueueItemSnapshot]:
        requested = tuple(dict.fromkeys(observation_refs))
        existing = {
            value.source_observation_ref: value
            for value in self._get_observation_items_sync(
                queue_ref=queue_ref,
                observation_refs=requested,
            )
        }
        try:
            from langfuse.api.annotation_queues.types.annotation_queue_object_type import (
                AnnotationQueueObjectType,
            )

            for observation_ref in requested:
                if observation_ref in existing:
                    continue
                value = self._client.api.annotation_queues.create_queue_item(
                    queue_ref,
                    object_id=observation_ref,
                    object_type=AnnotationQueueObjectType.OBSERVATION,
                )
                snapshot = self._item_snapshot(value)
                if snapshot.source_observation_ref != observation_ref:
                    raise LangfuseAnnotationQueueAdapterError(
                        "Langfuse returned another queue item object"
                    )
                existing[observation_ref] = snapshot
            return [existing[value] for value in requested]
        except LangfuseAnnotationQueueAdapterError:
            raise
        except Exception as exc:
            raise LangfuseAnnotationQueueAdapterError(
                "Langfuse annotation queue item creation failed"
            ) from exc

    async def list_queues(self, *, limit: int) -> list[AnnotationQueueDescriptor]:
        return await asyncio.to_thread(self._list_queues_sync, limit=limit)

    async def get_queue(self, *, queue_ref: str) -> AnnotationQueueDescriptor:
        return await asyncio.to_thread(self._get_queue_sync, queue_ref=queue_ref)

    async def ensure_observations(
        self,
        *,
        queue_ref: str,
        observation_refs: Sequence[str],
    ) -> list[AnnotationQueueItemSnapshot]:
        return await asyncio.to_thread(
            self._ensure_observations_sync,
            queue_ref=queue_ref,
            observation_refs=observation_refs,
        )

    async def get_observation_items(
        self,
        *,
        queue_ref: str,
        observation_refs: Sequence[str],
    ) -> list[AnnotationQueueItemSnapshot]:
        return await asyncio.to_thread(
            self._get_observation_items_sync,
            queue_ref=queue_ref,
            observation_refs=observation_refs,
        )

    @staticmethod
    def _digest_provider_evidence(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    def _latest_annotation_score(
        self,
        *,
        queue_ref: str,
        rule: PromotionQualityRule,
        source: PromotionEvidenceSource,
    ) -> Any | None:
        values: list[Any] = []
        cursor: str | None = None
        while True:
            response = self._client.api.scores_v3.get_many_v3(
                fields="details,subject,annotation",
                source="ANNOTATION",
                data_type=rule.score_data_type,
                config_id=rule.score_config_id,
                queue_id=queue_ref,
                trace_id=source.source_trace_ref,
                observation_id=source.source_observation_ref,
                limit=100,
                cursor=cursor,
            )
            values.extend(response.data)
            if len(values) > 1000:
                raise LangfuseAnnotationQueueAdapterError(
                    "Langfuse returned too many matching annotation scores"
                )
            cursor_value = getattr(response.meta, "cursor", None)
            if not isinstance(cursor_value, str) or not cursor_value:
                break
            cursor = cursor_value
        matches: list[Any] = []
        for value in values:
            subject = getattr(value, "subject", None)
            source_type = getattr(getattr(value, "source", None), "value", None)
            if source_type is None:
                source_type = getattr(value, "source", None)
            if (
                getattr(subject, "kind", None) != "observation"
                or getattr(subject, "id", None)
                != source.source_observation_ref
                or getattr(subject, "trace_id", None)
                != source.source_trace_ref
                or getattr(value, "config_id", None) != rule.score_config_id
                or getattr(value, "queue_id", None) != queue_ref
                or source_type != "ANNOTATION"
                or getattr(value, "data_type", None) != rule.score_data_type
            ):
                continue
            matches.append(value)
        if not matches:
            return None
        return max(
            matches,
            key=lambda value: (
                _required_provider_datetime(
                    getattr(value, "updated_at", None),
                    field_name="score update timestamp",
                ),
                _required_provider_text(
                    getattr(value, "id", None),
                    field_name="score identity",
                ),
            ),
        )

    def _observation_diversity_value(
        self,
        *,
        dimension: str,
        source: PromotionEvidenceSource,
    ) -> str | None:
        if dimension == "NONE":
            return "all"
        cursor: str | None = None
        while True:
            response = self._client.api.observations.get_many(
                fields="core,basic",
                trace_id=source.source_trace_ref,
                limit=1000,
                cursor=cursor,
            )
            for value in response.data:
                if getattr(value, "id", None) != source.source_observation_ref:
                    continue
                if dimension == "OBSERVATION_NAME":
                    raw = getattr(value, "name", None)
                elif dimension == "OBSERVATION_TYPE":
                    raw = getattr(value, "type", None)
                    raw = getattr(raw, "value", raw)
                elif dimension == "ENVIRONMENT":
                    raw = getattr(value, "environment", None)
                else:
                    raise LangfuseAnnotationQueueAdapterError(
                        "Promotion diversity dimension is unsupported"
                    )
                candidate = _bounded_metadata_text(raw)
                return candidate or None
            cursor_value = getattr(response.meta, "cursor", None)
            if not isinstance(cursor_value, str) or not cursor_value:
                return None
            cursor = cursor_value

    @staticmethod
    def _quality_passed(value: Any, rule: PromotionQualityRule) -> bool:
        if rule.score_data_type == "NUMERIC":
            return (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and rule.minimum_numeric_score is not None
                and float(value) >= rule.minimum_numeric_score
            )
        if rule.score_data_type == "BOOLEAN":
            return isinstance(value, bool) and value in rule.accepted_values
        if rule.score_data_type == "CATEGORICAL":
            return isinstance(value, str) and value in rule.accepted_values
        raise LangfuseAnnotationQueueAdapterError(
            "Promotion score data type is unsupported"
        )

    def _evaluate_promotion_evidence_sync(
        self,
        *,
        queue_ref: str,
        quality_rule: PromotionQualityRule,
        diversity_dimension: str,
        sources: Sequence[PromotionEvidenceSource],
    ) -> list[PromotionEvidenceItem]:
        require_langfuse_sdk_compatibility()
        if not sources or len(sources) > 20:
            raise LangfuseAnnotationQueueAdapterError(
                "Promotion evidence requires 1 to 20 Observations"
            )
        descriptor = self._get_queue_sync(queue_ref=queue_ref)
        if quality_rule.score_config_id not in descriptor.score_config_ids:
            raise LangfuseAnnotationQueueAdapterError(
                "Promotion score config is no longer attached to the queue"
            )
        try:
            evidence: list[PromotionEvidenceItem] = []
            for source in sources:
                score = self._latest_annotation_score(
                    queue_ref=queue_ref,
                    rule=quality_rule,
                    source=source,
                )
                bucket_value = self._observation_diversity_value(
                    dimension=diversity_dimension,
                    source=source,
                )
                score_value = getattr(score, "value", None)
                evidence.append(
                    PromotionEvidenceItem(
                        source_trace_ref=source.source_trace_ref,
                        source_observation_ref=source.source_observation_ref,
                        score_present=score is not None,
                        quality_passed=(
                            self._quality_passed(score_value, quality_rule)
                            if score is not None
                            else False
                        ),
                        diversity_bucket_present=bucket_value is not None,
                        score_evidence_digest=(
                            self._digest_provider_evidence(
                                {
                                    "id": getattr(score, "id", None),
                                    "config_id": getattr(
                                        score, "config_id", None
                                    ),
                                    "queue_id": getattr(score, "queue_id", None),
                                    "data_type": getattr(
                                        score, "data_type", None
                                    ),
                                    "value": score_value,
                                    "updated_at": _utc_iso(
                                        _required_provider_datetime(
                                            getattr(score, "updated_at", None),
                                            field_name="score update timestamp",
                                        )
                                    ),
                                    "trace_ref": source.source_trace_ref,
                                    "observation_ref": (
                                        source.source_observation_ref
                                    ),
                                }
                            )
                            if score is not None
                            else None
                        ),
                        diversity_bucket_digest=(
                            self._digest_provider_evidence(
                                {
                                    "dimension": diversity_dimension,
                                    "value": bucket_value,
                                }
                            )
                            if bucket_value is not None
                            else None
                        ),
                    )
                )
            return evidence
        except LangfuseAnnotationQueueAdapterError:
            raise
        except Exception as exc:
            raise LangfuseAnnotationQueueAdapterError(
                "Langfuse promotion evidence lookup failed"
            ) from exc

    async def evaluate_promotion_evidence(
        self,
        *,
        queue_ref: str,
        quality_rule: PromotionQualityRule,
        diversity_dimension: str,
        sources: Sequence[PromotionEvidenceSource],
    ) -> list[PromotionEvidenceItem]:
        return await asyncio.to_thread(
            self._evaluate_promotion_evidence_sync,
            queue_ref=queue_ref,
            quality_rule=quality_rule,
            diversity_dimension=diversity_dimension,
            sources=sources,
        )


class LangfuseTraceCandidateReader:
    """Reads content-free Observation metadata for a curation queue."""

    def __init__(self, *, client: Any) -> None:
        self._client = client

    def _list_candidates_sync(
        self,
        *,
        from_start_time: datetime,
        to_start_time: datetime,
        name: str | None,
        observation_type: str | None,
        environment: str | None,
        root_only: bool,
        limit: int,
    ) -> list[TraceDatasetCandidate]:
        require_langfuse_sdk_compatibility()
        try:
            response = self._client.api.observations.get_many(
                fields="core,basic,time",
                from_start_time=from_start_time,
                to_start_time=to_start_time,
                name=name,
                type=observation_type,
                environment=environment,
                is_root_observation=True if root_only else None,
                limit=limit,
            )
            candidates: list[TraceDatasetCandidate] = []
            for value in response.data:
                trace_ref = getattr(value, "trace_id", None)
                observation_ref = getattr(value, "id", None)
                start_time = getattr(value, "start_time", None)
                if (
                    not isinstance(trace_ref, str)
                    or not trace_ref
                    or not isinstance(observation_ref, str)
                    or not observation_ref
                    or not isinstance(start_time, datetime)
                ):
                    continue
                observation_type_value = getattr(value, "type", None)
                observation_type_text = getattr(
                    observation_type_value,
                    "value",
                    observation_type_value,
                )
                environment_value = _bounded_metadata_text(
                    getattr(value, "environment", None)
                )
                candidates.append(
                    TraceDatasetCandidate(
                        source_trace_ref=trace_ref,
                        source_observation_ref=observation_ref,
                        name=_bounded_metadata_text(
                            getattr(value, "name", None),
                            fallback="unnamed",
                        ),
                        observation_type=_bounded_metadata_text(
                            observation_type_text,
                            fallback="UNKNOWN",
                        ),
                        start_time=start_time,
                        end_time=(
                            getattr(value, "end_time", None)
                            if isinstance(
                                getattr(value, "end_time", None),
                                datetime,
                            )
                            else None
                        ),
                        environment=environment_value or None,
                    )
                )
            return candidates[:limit]
        except Exception as exc:
            raise LangfuseTraceDatasetMaterializerError(
                "Langfuse trace candidate query failed"
            ) from exc

    async def list_candidates(
        self,
        *,
        from_start_time: datetime,
        to_start_time: datetime,
        name: str | None,
        observation_type: str | None,
        environment: str | None,
        root_only: bool,
        limit: int,
    ) -> list[TraceDatasetCandidate]:
        return await asyncio.to_thread(
            self._list_candidates_sync,
            from_start_time=from_start_time,
            to_start_time=to_start_time,
            name=name,
            observation_type=observation_type,
            environment=environment,
            root_only=root_only,
            limit=limit,
        )


class LangfuseOpenAICompatibleSemanticAdapter:
    """Ephemerally reads Langfuse IO and returns bounded embedding evidence."""

    def __init__(
        self,
        *,
        client: Any,
        embedding_base_url: str,
        api_key: str = "",
        timeout_seconds: int = 15,
    ) -> None:
        parsed = urlsplit(embedding_base_url.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Semantic embedding base URL is invalid")
        self._client = client
        self._embedding_base_url = embedding_base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds

    def _observation_content_sync(
        self,
        *,
        source: SemanticClusteringSource,
        max_content_chars: int,
    ) -> str:
        require_langfuse_sdk_compatibility()
        try:
            response = self._client.api.observations.get_many(
                fields="core,basic,io",
                trace_id=source.source_trace_ref,
                limit=1000,
            )
            matches = [
                value
                for value in response.data
                if getattr(value, "id", None)
                == source.source_observation_ref
            ]
        except Exception as exc:
            raise LangfuseSemanticEmbeddingAdapterError(
                "Langfuse semantic source lookup failed"
            ) from exc
        if len(matches) != 1:
            raise LangfuseSemanticEmbeddingAdapterError(
                "Semantic source observation was not found exactly once"
            )
        observation = matches[0]
        parts: list[str] = []
        for label, value in (
            ("input", getattr(observation, "input", None)),
            ("output", getattr(observation, "output", None)),
        ):
            if value is None:
                continue
            text = _content_text(value).strip()
            if text:
                parts.append(f"{label}: {text}")
        content = "\n".join(parts)
        if not content:
            raise LangfuseSemanticEmbeddingAdapterError(
                "Semantic source observation has no embeddable IO"
            )
        return content[:max_content_chars]

    async def embed_observations(
        self,
        *,
        sources: Sequence[SemanticClusteringSource],
        model_ref: str,
        dimensions: int,
        max_content_chars: int,
    ) -> list[SemanticEmbeddingEvidence]:
        if not sources or len(sources) > _MAX_SEMANTIC_ITEMS:
            raise LangfuseSemanticEmbeddingAdapterError(
                "Semantic clustering requires between 1 and 20 sources"
            )
        exact_refs = {
            (value.source_trace_ref, value.source_observation_ref)
            for value in sources
        }
        if len(exact_refs) != len(sources):
            raise LangfuseSemanticEmbeddingAdapterError(
                "Semantic clustering sources must be unique"
            )
        contents = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._observation_content_sync,
                    source=source,
                    max_content_chars=max_content_chars,
                )
                for source in sources
            )
        )
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{self._embedding_base_url}/embeddings",
                    headers=headers,
                    json={"model": model_ref, "input": contents},
                )
            response.raise_for_status()
            if len(response.content) > _MAX_EMBEDDING_RESPONSE_BYTES:
                raise LangfuseSemanticEmbeddingAdapterError(
                    "Embedding response exceeds the safe size limit"
                )
            payload = response.json()
        except LangfuseSemanticEmbeddingAdapterError:
            raise
        except Exception as exc:
            raise LangfuseSemanticEmbeddingAdapterError(
                "Semantic embedding provider request failed"
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(sources):
            raise LangfuseSemanticEmbeddingAdapterError(
                "Embedding provider returned an unexpected item count"
            )
        ordered: list[list[float] | None] = [None] * len(sources)
        for fallback_index, item in enumerate(data):
            if not isinstance(item, dict):
                raise LangfuseSemanticEmbeddingAdapterError(
                    "Embedding provider returned a malformed item"
                )
            index = item.get("index", fallback_index)
            vector = item.get("embedding")
            if (
                not isinstance(index, int)
                or index < 0
                or index >= len(sources)
                or ordered[index] is not None
                or not isinstance(vector, list)
                or len(vector) != dimensions
            ):
                raise LangfuseSemanticEmbeddingAdapterError(
                    "Embedding provider returned invalid dimensions or order"
                )
            normalized: list[float] = []
            for component in vector:
                if not isinstance(component, (int, float)):
                    raise LangfuseSemanticEmbeddingAdapterError(
                        "Embedding vector contains a non-numeric component"
                    )
                numeric = float(component)
                if not math.isfinite(numeric):
                    raise LangfuseSemanticEmbeddingAdapterError(
                        "Embedding vector contains a non-finite component"
                    )
                normalized.append(numeric)
            ordered[index] = normalized
        evidence: list[SemanticEmbeddingEvidence] = []
        for source, content, vector in zip(sources, contents, ordered, strict=True):
            if vector is None:
                raise LangfuseSemanticEmbeddingAdapterError(
                    "Embedding provider omitted an item"
                )
            content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            embedding_digest = hashlib.sha256(
                json.dumps(
                    [format(value, ".9g") for value in vector],
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            evidence.append(
                SemanticEmbeddingEvidence(
                    source_trace_ref=source.source_trace_ref,
                    source_observation_ref=source.source_observation_ref,
                    content_digest=content_digest,
                    embedding_digest=embedding_digest,
                    vector=tuple(vector),
                )
            )
        return evidence


class LangfuseTraceDatasetMaterializer:
    """Provider adapter that keeps Trace and Dataset content in Langfuse."""

    def __init__(self, *, client: Any) -> None:
        self._client = client

    def _observations(
        self,
        *,
        trace_ref: str,
    ) -> list[Any]:
        observations: list[Any] = []
        cursor = None
        while True:
            response = self._client.api.observations.get_many(
                fields="core,basic,time,io",
                trace_id=trace_ref,
                limit=1000,
                cursor=cursor,
            )
            observations.extend(response.data)
            if len(observations) > _MAX_TRACE_OBSERVATIONS:
                raise LangfuseTraceDatasetMaterializerError(
                    "Trace contains more than 10000 observations"
                )
            cursor = getattr(response.meta, "cursor", None)
            if not cursor:
                return observations

    @staticmethod
    def _select_observation(
        observations: list[Any],
        *,
        observation_ref: str | None,
    ) -> Any:
        if observation_ref is not None:
            matching = [
                observation
                for observation in observations
                if getattr(observation, "id", None) == observation_ref
            ]
            if len(matching) != 1:
                raise LangfuseTraceDatasetMaterializerError(
                    "Selected observation was not found in the trace"
                )
            return matching[0]
        roots = [
            observation
            for observation in observations
            if getattr(observation, "is_root_observation", False) is True
            or getattr(observation, "parent_observation_id", None) is None
        ]
        if len(roots) != 1:
            raise LangfuseTraceDatasetMaterializerError(
                "Trace must have exactly one root observation or an explicit "
                "observation_ref"
            )
        return roots[0]

    def _dataset_items(
        self,
        *,
        dataset_name: str,
        version: datetime | None = None,
    ) -> list[Any]:
        items: list[Any] = []
        page = 1
        while True:
            response = self._client.api.dataset_items.list(
                dataset_name=dataset_name,
                version=version,
                page=page,
                limit=100,
            )
            items.extend(response.data)
            total_pages = int(getattr(response.meta, "total_pages", 1))
            if page >= total_pages:
                return items
            page += 1

    def _validate_dataset(
        self,
        *,
        provider_dataset_name: str,
        provider_dataset_ref: str,
    ) -> None:
        provider_dataset = self._client.api.datasets.get(
            provider_dataset_name
        )
        if getattr(provider_dataset, "id", None) != provider_dataset_ref:
            raise LangfuseTraceDatasetMaterializerError(
                "Langfuse Dataset identity no longer matches DuckDock"
            )

    def _selected_observation(
        self,
        *,
        trace_ref: str,
        observation_ref: str | None,
    ) -> tuple[str, Any, Any]:
        selected = self._select_observation(
            self._observations(trace_ref=trace_ref),
            observation_ref=observation_ref,
        )
        selected_ref = getattr(selected, "id", None)
        if not isinstance(selected_ref, str) or not selected_ref:
            raise LangfuseTraceDatasetMaterializerError(
                "Selected observation has no identity"
            )
        return (
            selected_ref,
            _provider_io(
                getattr(selected, "input", None),
                field_name="input",
            ),
            _provider_io(
                getattr(selected, "output", None),
                field_name="output",
            ),
        )

    def _upsert_dataset_item(
        self,
        *,
        dataset_public_id: str,
        provider_dataset_name: str,
        trace_ref: str,
        observation_ref: str,
        item_input: Any,
        expected_output: Any,
    ) -> str:
        item_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"duckdock-trace2dataset-v1:{dataset_public_id}:"
                    f"{trace_ref}:{observation_ref}"
                ),
            )
        )
        item = self._client.create_dataset_item(
            dataset_name=provider_dataset_name,
            id=item_id,
            input=item_input,
            expected_output=expected_output,
            source_trace_id=trace_ref,
            source_observation_id=observation_ref,
            metadata={
                "duckdock.dataset_public_id": dataset_public_id,
                "duckdock.materialization_schema": (
                    _TRACE_DATASET_SCHEMA_NAME
                ),
                "duckdock.materialization_schema_version": (
                    _TRACE_DATASET_SCHEMA_VERSION
                ),
            },
        )
        if getattr(item, "id", None) != item_id:
            raise LangfuseTraceDatasetMaterializerError(
                "Langfuse returned an unexpected Dataset item identity"
            )
        return item_id

    def _dataset_snapshot(
        self,
        *,
        provider_dataset_name: str,
        provider_dataset_ref: str,
        expected_item_refs: set[str],
    ) -> tuple[str, str, int]:
        current_items = self._dataset_items(
            dataset_name=provider_dataset_name
        )
        if not current_items:
            raise LangfuseTraceDatasetMaterializerError(
                "Langfuse Dataset is empty after materialization"
            )
        updated_at_values = [
            getattr(value, "updated_at")
            for value in current_items
            if isinstance(getattr(value, "updated_at", None), datetime)
        ]
        if not updated_at_values:
            raise LangfuseTraceDatasetMaterializerError(
                "Langfuse Dataset items have no update timestamps"
            )
        pinned_at = max(updated_at_values) + timedelta(milliseconds=1)
        pinned_items = self._dataset_items(
            dataset_name=provider_dataset_name,
            version=pinned_at,
        )
        manifest = sorted(
            (_item_manifest_record(value) for value in pinned_items),
            key=lambda value: value["id"] or "",
        )
        if not expected_item_refs.issubset(
            {value["id"] for value in manifest if value["id"]}
        ):
            raise LangfuseTraceDatasetMaterializerError(
                "Pinned Langfuse Dataset does not contain every new item"
            )
        provider_version_ref = _utc_iso(pinned_at)
        digest_payload = {
            "schema_name": _TRACE_DATASET_SCHEMA_NAME,
            "schema_version": _TRACE_DATASET_SCHEMA_VERSION,
            "provider_dataset_ref": provider_dataset_ref,
            "provider_version_ref": provider_version_ref,
            "items": manifest,
        }
        manifest_digest = hashlib.sha256(
            json.dumps(
                digest_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return provider_version_ref, manifest_digest, len(manifest)

    def _materialize_sync(
        self,
        *,
        dataset_public_id: str,
        provider_dataset_name: str,
        provider_dataset_ref: str,
        trace_ref: str,
        observation_ref: str | None,
    ) -> TraceDatasetMaterializationReceipt:
        require_langfuse_sdk_compatibility()
        try:
            self._validate_dataset(
                provider_dataset_name=provider_dataset_name,
                provider_dataset_ref=provider_dataset_ref,
            )
            selected_ref, item_input, expected_output = (
                self._selected_observation(
                    trace_ref=trace_ref,
                    observation_ref=observation_ref,
                )
            )
            item_id = self._upsert_dataset_item(
                dataset_public_id=dataset_public_id,
                provider_dataset_name=provider_dataset_name,
                trace_ref=trace_ref,
                observation_ref=selected_ref,
                item_input=item_input,
                expected_output=expected_output,
            )
            provider_version_ref, manifest_digest, item_count = (
                self._dataset_snapshot(
                    provider_dataset_name=provider_dataset_name,
                    provider_dataset_ref=provider_dataset_ref,
                    expected_item_refs={item_id},
                )
            )
            return TraceDatasetMaterializationReceipt(
                provider_dataset_ref=provider_dataset_ref,
                provider_dataset_item_ref=item_id,
                source_trace_ref=trace_ref,
                source_observation_ref=selected_ref,
                provider_version_ref=provider_version_ref,
                manifest_digest=manifest_digest,
                item_count=item_count,
            )
        except LangfuseTraceDatasetMaterializerError:
            raise
        except Exception as exc:
            raise LangfuseTraceDatasetMaterializerError(
                "Langfuse Trace2Dataset materialization failed"
            ) from exc

    def _materialize_batch_sync(
        self,
        *,
        dataset_public_id: str,
        provider_dataset_name: str,
        provider_dataset_ref: str,
        sources: Sequence[TraceDatasetBatchSource],
    ) -> TraceDatasetBatchMaterializationReceipt:
        require_langfuse_sdk_compatibility()
        if not sources or len(sources) > _MAX_CURATION_BATCH_ITEMS:
            raise LangfuseTraceDatasetMaterializerError(
                "Trace2Dataset batch must contain between 1 and 20 sources"
            )
        try:
            self._validate_dataset(
                provider_dataset_name=provider_dataset_name,
                provider_dataset_ref=provider_dataset_ref,
            )
            resolved: list[tuple[TraceDatasetBatchSource, Any, Any]] = []
            for source in sources:
                selected_ref, item_input, expected_output = (
                    self._selected_observation(
                        trace_ref=source.source_trace_ref,
                        observation_ref=source.source_observation_ref,
                    )
                )
                if selected_ref != source.source_observation_ref:
                    raise LangfuseTraceDatasetMaterializerError(
                        "Langfuse returned another observation identity"
                    )
                resolved.append((source, item_input, expected_output))

            item_receipts: list[TraceDatasetBatchItemReceipt] = []
            for source, item_input, expected_output in resolved:
                item_ref = self._upsert_dataset_item(
                    dataset_public_id=dataset_public_id,
                    provider_dataset_name=provider_dataset_name,
                    trace_ref=source.source_trace_ref,
                    observation_ref=source.source_observation_ref,
                    item_input=item_input,
                    expected_output=expected_output,
                )
                item_receipts.append(
                    TraceDatasetBatchItemReceipt(
                        source_trace_ref=source.source_trace_ref,
                        source_observation_ref=(
                            source.source_observation_ref
                        ),
                        provider_dataset_item_ref=item_ref,
                    )
                )
            provider_version_ref, manifest_digest, item_count = (
                self._dataset_snapshot(
                    provider_dataset_name=provider_dataset_name,
                    provider_dataset_ref=provider_dataset_ref,
                    expected_item_refs={
                        item.provider_dataset_item_ref
                        for item in item_receipts
                    },
                )
            )
            return TraceDatasetBatchMaterializationReceipt(
                provider_dataset_ref=provider_dataset_ref,
                provider_version_ref=provider_version_ref,
                manifest_digest=manifest_digest,
                item_count=item_count,
                items=tuple(item_receipts),
            )
        except LangfuseTraceDatasetMaterializerError:
            raise
        except Exception as exc:
            raise LangfuseTraceDatasetMaterializerError(
                "Langfuse Trace2Dataset batch materialization failed"
            ) from exc

    async def materialize(
        self,
        *,
        dataset_public_id: str,
        provider_dataset_name: str,
        provider_dataset_ref: str,
        trace_ref: str,
        observation_ref: str | None,
    ) -> TraceDatasetMaterializationReceipt:
        return await asyncio.to_thread(
            self._materialize_sync,
            dataset_public_id=dataset_public_id,
            provider_dataset_name=provider_dataset_name,
            provider_dataset_ref=provider_dataset_ref,
            trace_ref=trace_ref,
            observation_ref=observation_ref,
        )

    async def materialize_batch(
        self,
        *,
        dataset_public_id: str,
        provider_dataset_name: str,
        provider_dataset_ref: str,
        sources: Sequence[TraceDatasetBatchSource],
    ) -> TraceDatasetBatchMaterializationReceipt:
        return await asyncio.to_thread(
            self._materialize_batch_sync,
            dataset_public_id=dataset_public_id,
            provider_dataset_name=provider_dataset_name,
            provider_dataset_ref=provider_dataset_ref,
            sources=sources,
        )


class LangfuseExperimentRunner:
    """Langfuse v4 runner returning only bounded governance summaries."""

    def __init__(
        self,
        *,
        client: Any,
        target_adapter: EvaluationTargetPort,
        evaluation_adapter_factory: (
            Callable[[EvaluationRunRequest], EvaluationAdapterPort] | None
        ) = None,
        max_concurrency: int = 5,
    ) -> None:
        self._client = client
        self._target_adapter = target_adapter
        self._evaluation_adapter_factory = (
            evaluation_adapter_factory or (lambda _request: DeepEvalAdapter())
        )
        self._max_concurrency = max(1, min(max_concurrency, 50))

    def _run_sync(
        self,
        request: EvaluationRunRequest,
    ) -> EvaluationRunSummary:
        require_langfuse_sdk_compatibility()
        try:
            from langfuse import Evaluation as LangfuseEvaluation
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LangfuseExperimentRunnerError(
                "Langfuse SDK is unavailable"
            ) from exc

        try:
            dataset = self._client.get_dataset(
                request.dataset_name,
                version=request.dataset_version,
            )
        except Exception as exc:
            raise LangfuseExperimentRunnerError(
                "Langfuse dataset lookup failed"
            ) from exc
        if len(dataset.items) != request.expected_item_count:
            raise LangfuseExperimentRunnerError(
                "Pinned dataset item count does not match governance record"
            )
        if not dataset.items:
            raise LangfuseExperimentRunnerError(
                "Pinned dataset contains no evaluation items"
            )

        metric_spec = None
        evaluation_adapter = None
        if request.evaluator_implementation_ref == "rule://exact_match":
            metric_name = "exact_match"
            threshold = 1.0
        else:
            metric_spec = _metric_spec(
                request.evaluator_implementation_ref
            )
            metric_name = metric_spec.name
            threshold = metric_spec.threshold
            evaluation_adapter = self._evaluation_adapter_factory(request)

        def task(*, item, **_kwargs):
            metadata = (
                item.metadata if isinstance(item.metadata, dict) else None
            )
            return self._target_adapter.execute(
                target_type=request.target_type,
                target_ref=request.target_ref,
                target_digest=request.target_digest,
                item_input=item.input,
                item_metadata=metadata,
            )

        def evaluator(
            *,
            input,
            output,
            expected_output=None,
            **_kwargs,
        ):
            if request.evaluator_implementation_ref == "rule://exact_match":
                score = 1.0 if output == expected_output else 0.0
                passed = score == 1.0
            else:
                if metric_spec is None or evaluation_adapter is None:
                    raise LangfuseExperimentRunnerError(
                        "Evaluator configuration invariant was not satisfied"
                    )
                summaries = evaluation_adapter.evaluate(
                    case=EphemeralEvaluationCase(
                        input_text=_content_text(input),
                        actual_output=_content_text(output),
                        expected_output=(
                            _content_text(expected_output)
                            if expected_output is not None
                            else None
                        ),
                    ),
                    metrics=[metric_spec],
                )
                score = summaries[0].score
                passed = summaries[0].passed
            return LangfuseEvaluation(
                name=metric_name,
                value=score,
                metadata={
                    "duckdock.threshold": threshold,
                    "duckdock.passed": passed,
                },
            )

        try:
            result = dataset.run_experiment(
                name=f"duckdock.{request.experiment_public_id}",
                run_name=f"duckdock.{request.evaluation_public_id}",
                description="DuckDock governed evaluation",
                task=task,
                evaluators=[evaluator],
                max_concurrency=self._max_concurrency,
                metadata={
                    "duckdock.namespace_id": str(request.namespace_id),
                    "duckdock.experiment_public_id": (
                        request.experiment_public_id
                    ),
                    "duckdock.evaluation_public_id": (
                        request.evaluation_public_id
                    ),
                    "duckdock.target_digest": request.target_digest,
                    "duckdock.evaluator_config_digest": (
                        request.evaluator_config_digest
                    ),
                },
            )
        except Exception as exc:
            raise LangfuseExperimentRunnerError(
                "Langfuse experiment execution failed"
            ) from exc

        scores: list[float] = []
        passed_count = 0
        manifest_items: list[dict[str, Any]] = []
        for item_result in result.item_results:
            dataset_item_ref = getattr(
                getattr(item_result, "item", None),
                "id",
                None,
            )
            trace_ref = getattr(item_result, "trace_id", None)
            if (
                not isinstance(dataset_item_ref, str)
                or not dataset_item_ref
                or not isinstance(trace_ref, str)
                or not trace_ref
            ):
                raise LangfuseExperimentRunnerError(
                    "Langfuse result item identity is incomplete"
                )
            matching = [
                value
                for value in item_result.evaluations
                if value.name == metric_name
                and isinstance(value.value, (int, float))
                and not isinstance(value.value, bool)
            ]
            item_score = None
            item_passed = None
            if len(matching) == 1:
                item_score = float(matching[0].value)
                scores.append(item_score)
                passed_value = (matching[0].metadata or {}).get(
                    "duckdock.passed"
                )
                item_passed = passed_value is True or (
                    passed_value is None and item_score >= threshold
                )
                if item_passed:
                    passed_count += 1
            manifest_items.append(
                {
                    "dataset_item_ref": dataset_item_ref,
                    "trace_ref": trace_ref,
                    "metric_name": metric_name,
                    "score": item_score,
                    "passed": item_passed,
                }
            )

        provider_ref = result.dataset_run_id or result.experiment_id
        if not provider_ref:
            raise LangfuseExperimentRunnerError(
                "Langfuse returned no experiment identity"
            )
        total_count = request.expected_item_count
        processed_count = len(result.item_results)
        scored_count = len(scores)
        if processed_count > total_count or scored_count > processed_count:
            raise LangfuseExperimentRunnerError(
                "Langfuse result counts exceed the pinned dataset"
            )
        failed_count = scored_count - passed_count
        error_count = total_count - scored_count
        complete = (
            processed_count == total_count
            and scored_count == total_count
        )
        if complete:
            completeness = "COMPLETE"
            loss_reason = None
        else:
            completeness = "PARTIAL"
            item_loss = processed_count < total_count
            score_loss = scored_count < processed_count
            if item_loss and score_loss:
                loss_reason = "provider_item_and_score_loss"
            elif item_loss:
                loss_reason = "provider_item_loss"
            else:
                loss_reason = "provider_score_loss"
        manifest_payload = {
            "provider": "LANGFUSE",
            "provider_dataset_ref": request.provider_dataset_ref,
            "provider_experiment_ref": provider_ref,
            "schema_name": LANGFUSE_RESULT_SCHEMA_NAME,
            "schema_version": LANGFUSE_RESULT_SCHEMA_VERSION,
            "expected_count": total_count,
            "processed_count": processed_count,
            "scored_count": scored_count,
            "items": sorted(
                manifest_items,
                key=lambda value: value["dataset_item_ref"],
            ),
        }
        content_digest = hashlib.sha256(
            json.dumps(
                manifest_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        score = sum(scores) / scored_count if scores else None
        manifest = EvaluationResultManifestSummary(
            provider="LANGFUSE",
            provider_dataset_ref=request.provider_dataset_ref,
            provider_experiment_ref=provider_ref,
            schema_name=LANGFUSE_RESULT_SCHEMA_NAME,
            schema_version=LANGFUSE_RESULT_SCHEMA_VERSION,
            content_digest=content_digest,
            expected_count=total_count,
            processed_count=processed_count,
            scored_count=scored_count,
            passed_count=passed_count,
            failed_count=failed_count,
            error_count=error_count,
            completeness=completeness,
            loss_reason=loss_reason,
        )
        return EvaluationRunSummary(
            provider_evaluation_ref=provider_ref,
            score=(
                max(0.0, min(score, 1.0))
                if score is not None
                else None
            ),
            total_count=total_count,
            processed_count=processed_count,
            scored_count=scored_count,
            passed_count=passed_count,
            failed_count=failed_count,
            error_count=error_count,
            completeness=completeness,
            loss_reason=loss_reason,
            result_manifest=manifest,
        )

    async def run(
        self,
        *,
        request: EvaluationRunRequest,
    ) -> EvaluationRunSummary:
        return await asyncio.to_thread(self._run_sync, request)


def build_langfuse_experiment_runner(
    *,
    compatibility_profile: str,
    client: Any,
    target_adapter: EvaluationTargetPort,
    evaluation_adapter_factory: (
        Callable[[EvaluationRunRequest], EvaluationAdapterPort] | None
    ) = None,
    max_concurrency: int = 5,
) -> LangfuseExperimentRunner:
    if compatibility_profile != LANGFUSE_COMPATIBILITY_PROFILE:
        raise LangfuseExperimentRunnerError(
            "Unsupported Langfuse compatibility profile: "
            f"{compatibility_profile}"
        )
    return LangfuseExperimentRunner(
        client=client,
        target_adapter=target_adapter,
        evaluation_adapter_factory=evaluation_adapter_factory,
        max_concurrency=max_concurrency,
    )
