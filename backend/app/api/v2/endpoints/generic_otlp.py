"""Reporter-credential-authenticated Generic OTLP projection endpoint."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse

from app.api.v2.endpoints.executions import (
    get_reporter_execution_identity,
)
from app.core.deps import DB
from app.services.generic_otlp_service import (
    GenericOtlpError,
    GenericOtlpPayloadError,
    GenericOtlpSinkError,
    ingest_generic_otlp_json,
)
from app.models.fleet import AdapterProfile
from app.services.fleet_service import (
    FleetHandshakeRequiredError,
    require_active_adapter_handshake,
)
from app.services.reporter_identity_service import ReporterExecutionIdentity
from app.services.telemetry_service import (
    TelemetryError,
    TelemetryNotFoundError,
    TelemetryTenantMismatchError,
    get_telemetry_sink,
)


router = APIRouter(
    prefix="/reporter/telemetry-sinks",
    tags=["generic-otlp"],
)
MAX_GENERIC_OTLP_BODY_BYTES = 1_048_576
ReporterIdentity = Annotated[
    ReporterExecutionIdentity,
    Depends(get_reporter_execution_identity),
]


def _otlp_error(status_code: int, error: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        # OTLP/HTTP failures use the JSON form of google.rpc.Status. The code
        # field is optional in OTLP, so keep only one bounded safe message.
        content={"message": error},
    )


@router.post(
    "/{sink_public_id}/v1/traces",
    operation_id="ingestGenericOtlpTraces",
    response_class=JSONResponse,
    responses={
        200: {
            "description": (
                "OTLP export accepted, optionally with partialSuccess."
            )
        },
        400: {"description": "Malformed or unsupported OTLP JSON."},
        404: {"description": "TelemetrySink not visible to this Runtime."},
        413: {"description": "OTLP JSON exceeds the projection limit."},
        415: {"description": "Only uncompressed OTLP JSON is accepted."},
    },
)
async def ingest_generic_otlp_traces(
    sink_public_id: str,
    request: Request,
    db: DB,
    identity: ReporterIdentity,
    duckdock_handshake_id: Annotated[
        str | None,
        Header(alias="DuckDock-Handshake-Id"),
    ] = None,
) -> JSONResponse:
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type != "application/json":
        return _otlp_error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "otlp_json_required",
        )
    content_encoding = request.headers.get("content-encoding", "identity")
    if content_encoding not in ("", "identity"):
        return _otlp_error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "uncompressed_otlp_json_required",
        )
    body = await request.body()
    if len(body) > MAX_GENERIC_OTLP_BODY_BYTES:
        return _otlp_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "otlp_payload_too_large",
        )
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _otlp_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_otlp_json",
        )

    try:
        if duckdock_handshake_id is not None:
            await require_active_adapter_handshake(
                db,
                identity=identity,
                handshake_public_id=duckdock_handshake_id,
                profile=AdapterProfile.GENERIC_OTLP_BRIDGE,
                required_capabilities={
                    "otel_trace_correlation",
                    "metadata_projection",
                },
            )
        sink = await get_telemetry_sink(
            db,
            public_id=sink_public_id,
            namespace_id=identity.namespace_id,
        )
        result = await ingest_generic_otlp_json(
            db,
            identity=identity,
            sink=sink,
            payload=payload,
        )
    except (TelemetryNotFoundError, TelemetryTenantMismatchError):
        return _otlp_error(
            status.HTTP_404_NOT_FOUND,
            "telemetry_sink_not_found",
        )
    except GenericOtlpSinkError:
        return _otlp_error(
            status.HTTP_409_CONFLICT,
            "telemetry_sink_inactive",
        )
    except FleetHandshakeRequiredError:
        return _otlp_error(
            status.HTTP_409_CONFLICT,
            "adapter_handshake_required",
        )
    except GenericOtlpPayloadError:
        return _otlp_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_otlp_payload",
        )
    except (GenericOtlpError, TelemetryError):
        # Do not echo exception text: provider bodies or rejected attributes
        # may be embedded in adapter errors.
        return _otlp_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "generic_otlp_projection_rejected",
        )

    content: dict[str, object] = {}
    if result.rejected_spans:
        content["partialSuccess"] = {
            "rejectedSpans": str(result.rejected_spans),
            "errorMessage": (
                result.rejection_reason or "mapping_quarantined"
            ),
        }
    return JSONResponse(status_code=status.HTTP_200_OK, content=content)
