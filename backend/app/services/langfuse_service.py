from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.services.langfuse_compatibility import (
    LANGFUSE_OBSERVATIONS_FIELDS,
    require_langfuse_sdk_compatibility,
)

logger = logging.getLogger(__name__)

Langfuse: Any
try:
    from langfuse import Langfuse as _Langfuse
except Exception:  # pragma: no cover
    Langfuse = None
else:
    Langfuse = _Langfuse


class LangfuseService:
    def __init__(self) -> None:
        self._client = None

    def is_enabled(self) -> bool:
        return bool(
            settings.CLINIC_LANGFUSE_ENABLED
            and settings.LANGFUSE_PUBLIC_KEY
            and settings.LANGFUSE_SECRET_KEY
            and Langfuse is not None
        )

    def client(self):
        if not self.is_enabled():
            return None
        if self._client is None:
            require_langfuse_sdk_compatibility()
            self._client = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=settings.LANGFUSE_BASE_URL,
                environment=settings.LANGFUSE_ENVIRONMENT,
                timeout=settings.LANGFUSE_TIMEOUT_SECONDS,
            )
        return self._client

    def get_trace_url(self, *, trace_id: str) -> str | None:
        """Build a browser-safe trace URL without making telemetry mandatory."""
        client = self.client()
        if client is None:
            return None
        try:
            trace_url = client.get_trace_url(trace_id=trace_id)
        except Exception as exc:  # pragma: no cover - provider failure isolation
            logger.warning("Langfuse trace URL lookup failed: %s", exc)
            return None
        if not trace_url:
            return None

        public_base_url = settings.LANGFUSE_PUBLIC_BASE_URL.rstrip("/")
        marker = "/project/"
        if public_base_url and marker in trace_url:
            return f"{public_base_url}{marker}{trace_url.split(marker, 1)[1]}"
        return trace_url

    @contextmanager
    def start_clinic_evaluation(
        self,
        *,
        evaluation_id: int | None = None,
        namespace_id: int | None = None,
        namespace_name: str,
        skill_count: int,
        mode: str,
        rubric_version: str,
        prompt_version: str,
    ):
        client = self.client()
        if client is None:
            yield None
            return

        with client.start_as_current_observation(
            name="clinic-evaluation",
            as_type="span",
            input={
                "namespace": namespace_name,
                "namespace_id": namespace_id,
                "clinic_evaluation_id": evaluation_id,
                "skill_count": skill_count,
                "mode": mode,
            },
            metadata={
                "rubric_version": rubric_version,
                "prompt_version": prompt_version,
                "clinic_evaluation_id": evaluation_id,
                "namespace_id": namespace_id,
                "namespace_name": namespace_name,
            },
        ) as span:
            try:
                yield span
            finally:
                try:
                    client.flush()
                except Exception as exc:  # pragma: no cover
                    logger.warning("Langfuse flush failed: %s", exc)

    @contextmanager
    def start_judge_generation(
        self,
        *,
        parent_span,
        dimension_id: str,
        model: str,
        prompt_version: str,
        rubric_version: str,
        facts_summary: str,
        sampled_skill_names: list[str],
    ):
        if parent_span is None:
            yield None
            return

        with parent_span.start_as_current_observation(
            name=f"clinic-judge-{dimension_id}",
            as_type="generation",
            model=model,
            input={
                "dimension": dimension_id,
                "facts_summary": facts_summary,
                "sampled_skills": sampled_skill_names,
            },
            metadata={
                "prompt_version": prompt_version,
                "rubric_version": rubric_version,
            },
        ) as generation:
            yield generation

    def get_trace_link_for_evaluation(
        self,
        *,
        evaluation_id: int,
        created_at: datetime | None = None,
    ) -> dict[str, str] | None:
        client = self.client()
        if client is None:
            return None

        try:
            from_timestamp = None
            to_timestamp = None
            if created_at is not None:
                base = created_at.astimezone(timezone.utc)
                from_timestamp = base - timedelta(minutes=2)
                to_timestamp = base + timedelta(hours=1)

            observations = client.api.observations.get_many(
                fields=LANGFUSE_OBSERVATIONS_FIELDS,
                name="clinic-evaluation",
                limit=20,
                from_start_time=from_timestamp,
                to_start_time=to_timestamp,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Langfuse trace lookup failed: %s", exc)
            return None

        for observation in observations.data:
            metadata = observation.metadata or {}
            if metadata.get("clinic_evaluation_id") == evaluation_id:
                trace_id = observation.trace_id
                if not trace_id:
                    continue
                return {
                    "trace_id": trace_id,
                    "trace_url": self.get_trace_url(trace_id=trace_id) or "",
                }
        return None


langfuse_service = LangfuseService()
