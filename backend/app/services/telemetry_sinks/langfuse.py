from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx

from app.schemas.webhook import validate_external_url
from app.services.ssrf import ssrf_safe_request
from app.services.telemetry_ports import (
    TelemetrySinkPort,
    TraceConfirmation,
    TraceConfirmationRequest,
)


_OTEL_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_OTLP_SUFFIXES = (
    "/api/public/otel/v1/traces",
    "/api/public/otel",
)


def _langfuse_origin(endpoint: str) -> str:
    value = endpoint.strip().rstrip("/")
    validate_external_url(value)
    parts = urlsplit(value)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(
            "Langfuse endpoint cannot contain credentials, query, or fragment"
        )
    path = parts.path.rstrip("/")
    for suffix in _OTLP_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


def _iso8601(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class LangfuseTelemetrySinkPort(TelemetrySinkPort):
    """Confirm Collector-exported traces through Langfuse Observations API v2.

    Export remains in the Collector. This adapter only performs a bounded,
    metadata-only lookup and constructs a provider UI reference.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        public_key: str,
        secret_key: str,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not public_key or not secret_key:
            raise ValueError("Langfuse project credentials are required")
        if ":" in public_key or any(
            ord(character) < 32 for character in public_key + secret_key
        ):
            raise ValueError("Langfuse project credentials are malformed")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("timeout_seconds must be in (0, 60]")
        self.origin = _langfuse_origin(endpoint)
        self.public_key = public_key
        self.secret_key = secret_key
        self.timeout_seconds = timeout_seconds
        self.client = client

    async def confirm_trace(
        self,
        request: TraceConfirmationRequest,
    ) -> TraceConfirmation:
        if _OTEL_TRACE_ID.fullmatch(request.source_trace_id) is None:
            return TraceConfirmation(
                confirmed=False,
                external_trace_id=request.source_trace_id,
                external_session_id=request.source_session_id,
                error_code="trace_id_invalid",
            )

        now = datetime.now(timezone.utc)
        started_at = request.started_at or now
        ended_at = request.ended_at or now
        query = urlencode(
            {
                "fields": "core",
                "traceId": request.source_trace_id,
                "fromStartTime": _iso8601(started_at - timedelta(minutes=5)),
                "toStartTime": _iso8601(
                    max(ended_at, now) + timedelta(minutes=5)
                ),
                "limit": "1",
            }
        )
        url = f"{self.origin}/api/public/v2/observations?{query}"
        encoded_credentials = base64.b64encode(
            f"{self.public_key}:{self.secret_key}".encode("utf-8")
        ).decode("ascii")
        try:
            response = await ssrf_safe_request(
                "GET",
                url,
                timeout=self.timeout_seconds,
                follow_redirects=False,
                client=self.client,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Basic {encoded_credentials}",
                },
            )
        except (httpx.HTTPError, ValueError):
            return TraceConfirmation(
                confirmed=False,
                external_trace_id=request.source_trace_id,
                external_session_id=request.source_session_id,
                error_code="provider_unavailable",
            )

        error_code = self._response_error_code(response.status_code)
        if error_code is not None:
            return TraceConfirmation(
                confirmed=False,
                external_trace_id=request.source_trace_id,
                external_session_id=request.source_session_id,
                error_code=error_code,
            )
        try:
            payload = response.json()
            observations = payload["data"]
        except (KeyError, TypeError, ValueError):
            observations = None
        if not isinstance(observations, list):
            return TraceConfirmation(
                confirmed=False,
                external_trace_id=request.source_trace_id,
                external_session_id=request.source_session_id,
                error_code="provider_response_invalid",
            )

        matching = next(
            (
                observation
                for observation in observations
                if isinstance(observation, dict)
                and observation.get("traceId") == request.source_trace_id
            ),
            None,
        )
        if matching is None:
            return TraceConfirmation(
                confirmed=False,
                external_trace_id=request.source_trace_id,
                external_session_id=request.source_session_id,
                error_code="trace_not_found",
            )

        trace_url = None
        project_id = matching.get("projectId")
        if isinstance(project_id, str) and _PROJECT_ID.fullmatch(project_id):
            trace_url = (
                f"{self.origin}/project/{quote(project_id, safe='')}/traces/"
                f"{request.source_trace_id}"
            )
        return TraceConfirmation(
            confirmed=True,
            external_trace_id=request.source_trace_id,
            external_session_id=request.source_session_id,
            trace_url=trace_url,
        )

    @staticmethod
    def _response_error_code(status_code: int) -> str | None:
        if 200 <= status_code < 300:
            return None
        if status_code in {401, 403}:
            return "provider_auth_failed"
        if status_code == 404:
            return "provider_api_unsupported"
        if status_code == 429:
            return "provider_rate_limited"
        if status_code >= 500:
            return "provider_unavailable"
        return "provider_error"
