from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = PROJECT_ROOT / "specs" / "008-duckdock-2-foundation" / "contracts"
OPENAPI_PATH = CONTRACTS_DIR / "openapi-v2.yaml"
QUICKSTART_PATH = PROJECT_ROOT / "specs" / "008-duckdock-2-foundation" / "quickstart.md"

REPORTER_PATHS = {
    "/api/v2/reporter/sessions": "startReporterSession",
    "/api/v2/reporter/sessions/{session_public_id}/complete": "completeReporterSession",
    "/api/v2/reporter/runs": "startReporterRun",
    "/api/v2/reporter/runs/{run_public_id}/complete": "completeReporterRun",
}
REPORTER_START_PATHS = {
    "/api/v2/reporter/sessions",
    "/api/v2/reporter/runs",
}
AGENT_RUN_PATHS = {
    "/api/v2/agent-runs": "listAgentRuns",
    "/api/v2/agent-runs/{run_public_id}": "getAgentRun",
}
ARTIFACT_PATH = "/api/v2/agent-runs/{run_public_id}/artifacts"
GENERIC_OTLP_PATH = (
    "/api/v2/reporter/telemetry-sinks/{sink_public_id}/v1/traces"
)
PACK_IMPORT_PATHS = {
    "/api/v2/reporter/pack-imports": {
        "post": "createPackImport",
    },
    "/api/v2/reporter/pack-imports/{public_id}": {
        "get": "getPackImport",
    },
    "/api/v2/reporter/pack-imports/{public_id}/finalize": {
        "post": "finalizePackImport",
    },
    "/api/v2/reporter/pack-imports/{public_id}/multipart": {
        "get": "getPackMultipartState",
    },
    "/api/v2/reporter/pack-imports/{public_id}/multipart/parts/{part_number}": {
        "post": "createPackMultipartPartUrl",
    },
    "/api/v2/reporter/pack-imports/{public_id}/multipart/complete": {
        "post": "completePackMultipartUpload",
    },
    "/api/v2/reporter/pack-import-batches": {
        "post": "createPackImportBatch",
    },
    "/api/v2/reporter/pack-import-batches/{stream_id}/cursor": {
        "get": "getPackImportBatchCursor",
    },
    "/api/v2/reporter/pack-exports": {
        "post": "createPackExport",
    },
    "/api/v2/reporter/evaluation-replays": {
        "post": "replayEvaluationResult",
    },
}
FLEET_PATHS = {
    "/api/v2/reporter/handshakes": {
        "post": "negotiateAdapterHandshake",
    },
    "/api/v2/reporter/heartbeats": {
        "post": "recordAdapterHeartbeat",
    },
    "/api/v2/fleet/runtimes": {
        "get": "getFleetRuntimeSummary",
    },
}
TELEMETRY_SINK_PATHS = {
    "/api/v2/telemetry-sinks": {
        "get": "listTelemetrySinks",
        "post": "createTelemetrySink",
    },
    "/api/v2/telemetry-sinks/{public_id}": {
        "get": "getTelemetrySink",
        "patch": "updateTelemetrySink",
    },
    "/api/v2/telemetry-sinks/{public_id}/disable": {
        "post": "disableTelemetrySink",
    },
}
OUTBOX_PATHS = {
    "/api/v2/outbox/health": {
        "get": "getOutboxHealth",
    },
    "/api/v2/outbox/{event_id}/retry": {
        "post": "retryOutboxEvent",
    },
}
RECONCILIATION_PATHS = {
    "/api/v2/reconciliation/health": {
        "get": "getV1V2ReconciliationHealth",
    },
    "/api/v2/reconciliation/work-traces/{work_trace_id}": {
        "get": "getV1V2WorkTraceReconciliation",
        "post": "reconcileV1V2WorkTrace",
    },
}


def _load_openapi() -> dict[str, Any]:
    with OPENAPI_PATH.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    assert isinstance(value, dict)
    return value


def _resolve_component_ref(spec: dict[str, Any], ref: str) -> dict[str, Any]:
    assert ref.startswith("#/components/")
    current: Any = spec
    for part in ref[2:].split("/"):
        current = current[part]
    assert isinstance(current, dict)
    return current


def _operation(spec: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    operation = spec["paths"][path][method]
    assert isinstance(operation, dict)
    return operation


def _parameter_refs(operation: dict[str, Any]) -> set[str]:
    return {
        parameter["$ref"]
        for parameter in operation.get("parameters", [])
        if isinstance(parameter, dict) and "$ref" in parameter
    }


def _matches_openapi_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[expected]


def _validate_openapi_example(value: Any, schema: dict[str, Any], spec: dict[str, Any]) -> None:
    if "$ref" in schema:
        _validate_openapi_example(value, _resolve_component_ref(spec, schema["$ref"]), spec)
        return
    if "const" in schema:
        assert value == schema["const"]
    if "enum" in schema:
        assert value in schema["enum"]
    if "oneOf" in schema:
        matches = 0
        for candidate in schema["oneOf"]:
            try:
                _validate_openapi_example(value, candidate, spec)
            except AssertionError:
                continue
            matches += 1
        assert matches == 1
        return

    declared_type = schema.get("type")
    if declared_type is not None:
        expected = declared_type if isinstance(declared_type, list) else [declared_type]
        assert any(_matches_openapi_type(value, item) for item in expected)

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        assert set(schema.get("required", [])) <= set(value)
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(properties)
        property_names = schema.get("propertyNames", {})
        for key, child in value.items():
            if "enum" in property_names:
                assert key in property_names["enum"]
            if key in properties:
                _validate_openapi_example(child, properties[key], spec)
    elif isinstance(value, list):
        if "maxItems" in schema:
            assert len(value) <= schema["maxItems"]
        if "items" in schema:
            for item in value:
                _validate_openapi_example(item, schema["items"], spec)


def test_openapi_v2_version_paths_and_operation_ids() -> None:
    spec = _load_openapi()

    assert spec["openapi"] == "3.1.0"
    assert spec["jsonSchemaDialect"] == "https://json-schema.org/draft/2020-12/schema"
    expected_paths = (
        set(REPORTER_PATHS)
        | set(AGENT_RUN_PATHS)
        | {ARTIFACT_PATH}
        | {GENERIC_OTLP_PATH}
        | set(PACK_IMPORT_PATHS)
        | set(FLEET_PATHS)
        | set(TELEMETRY_SINK_PATHS)
        | set(OUTBOX_PATHS)
        | set(RECONCILIATION_PATHS)
    )
    assert expected_paths <= set(spec["paths"])
    assert all(not path.startswith("/api/v1") for path in spec["paths"])
    assert spec["components"]["schemas"]["AdapterProfile"]["enum"] == [
        "openclaw-reporter",
        "hermes-reporter",
        "generic-otlp-bridge",
        "pack-atif-import",
    ]

    operation_ids: list[str] = []
    for path, expected_operation_id in REPORTER_PATHS.items():
        operation = _operation(spec, path, "post")
        assert operation["operationId"] == expected_operation_id
        operation_ids.append(operation["operationId"])
    for path, expected_operation_id in AGENT_RUN_PATHS.items():
        operation = _operation(spec, path, "get")
        assert operation["operationId"] == expected_operation_id
        operation_ids.append(operation["operationId"])
    for method, expected_operation_id in {
        "get": "listAgentRunArtifacts",
        "post": "registerAgentRunArtifact",
    }.items():
        operation = _operation(spec, ARTIFACT_PATH, method)
        assert operation["operationId"] == expected_operation_id
        operation_ids.append(operation["operationId"])
    generic_otlp = _operation(spec, GENERIC_OTLP_PATH, "post")
    assert generic_otlp["operationId"] == "ingestGenericOtlpTraces"
    operation_ids.append(generic_otlp["operationId"])
    for path, methods in PACK_IMPORT_PATHS.items():
        for method, expected_operation_id in methods.items():
            operation = _operation(spec, path, method)
            assert operation["operationId"] == expected_operation_id
            operation_ids.append(operation["operationId"])
    for path, methods in FLEET_PATHS.items():
        for method, expected_operation_id in methods.items():
            operation = _operation(spec, path, method)
            assert operation["operationId"] == expected_operation_id
            operation_ids.append(operation["operationId"])
    for path, methods in TELEMETRY_SINK_PATHS.items():
        for method, expected_operation_id in methods.items():
            operation = _operation(spec, path, method)
            assert operation["operationId"] == expected_operation_id
            operation_ids.append(operation["operationId"])
    for path, methods in OUTBOX_PATHS.items():
        for method, expected_operation_id in methods.items():
            operation = _operation(spec, path, method)
            assert operation["operationId"] == expected_operation_id
            operation_ids.append(operation["operationId"])
    for path, methods in RECONCILIATION_PATHS.items():
        for method, expected_operation_id in methods.items():
            operation = _operation(spec, path, method)
            assert operation["operationId"] == expected_operation_id
            operation_ids.append(operation["operationId"])

    assert len(operation_ids) == len(set(operation_ids))


def test_reporter_operations_require_bearer_auth_and_idempotency() -> None:
    spec = _load_openapi()
    security_schemes = spec["components"]["securitySchemes"]
    reporter_auth = security_schemes["ReporterBearerAuth"]

    assert reporter_auth["type"] == "http"
    assert reporter_auth["scheme"] == "bearer"
    assert reporter_auth["bearerFormat"] == "DuckDock Reporter Token"

    idempotency = spec["components"]["parameters"]["IdempotencyKey"]
    assert idempotency["name"] == "Idempotency-Key"
    assert idempotency["in"] == "header"
    assert idempotency["required"] is True

    for path in REPORTER_PATHS:
        operation = _operation(spec, path, "post")
        assert operation["security"] == [{"ReporterBearerAuth": []}]
        assert "#/components/parameters/IdempotencyKey" in _parameter_refs(operation)
        assert operation["requestBody"]["required"] is True
        assert "application/json" in operation["requestBody"]["content"]


def test_agent_run_reads_require_user_bearer_auth() -> None:
    spec = _load_openapi()
    user_auth = spec["components"]["securitySchemes"]["UserBearerAuth"]

    assert user_auth["type"] == "http"
    assert user_auth["scheme"] == "bearer"
    assert user_auth["bearerFormat"] == "JWT"

    for path in AGENT_RUN_PATHS:
        operation = _operation(spec, path, "get")
        assert operation["security"] == [{"UserBearerAuth": []}]
    for method in ("get", "post"):
        assert _operation(spec, ARTIFACT_PATH, method)["security"] == [
            {"UserBearerAuth": []}
        ]
    for path, methods in TELEMETRY_SINK_PATHS.items():
        for method in methods:
            assert _operation(spec, path, method)["security"] == [
                {"UserBearerAuth": []}
            ]
    for path, methods in OUTBOX_PATHS.items():
        for method in methods:
            assert _operation(spec, path, method)["security"] == [
                {"UserBearerAuth": []}
            ]
    for path, methods in RECONCILIATION_PATHS.items():
        for method in methods:
            assert _operation(spec, path, method)["security"] == [
                {"UserBearerAuth": []}
            ]


def test_generic_otlp_uses_reporter_identity_and_otlp_partial_success() -> None:
    spec = _load_openapi()
    operation = _operation(spec, GENERIC_OTLP_PATH, "post")
    assert operation["security"] == [{"ReporterBearerAuth": []}]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OtlpTraceExportRequest"
    }
    response = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response == {
        "$ref": "#/components/schemas/OtlpTraceExportResponse"
    }
    projection = spec["components"]["schemas"]["OtlpTraceExportRequest"]
    assert "resourceSpans" in projection["required"]
    assert {
        "prompt",
        "completion",
        "tool_arguments",
        "tool_result",
    }.isdisjoint(projection["properties"])


def test_pack_import_contract_is_strict_metadata_only_and_reporter_scoped() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]
    for path, methods in PACK_IMPORT_PATHS.items():
        for method in methods:
            assert _operation(spec, path, method)["security"] == [
                {"ReporterBearerAuth": []}
            ]
    manifest = schemas["PackManifest"]
    payload = schemas["PackPayloadManifest"]
    import_state = schemas["PackImport"]
    assert manifest["additionalProperties"] is False
    assert payload["additionalProperties"] is False
    assert import_state["additionalProperties"] is False
    assert payload["properties"]["path"]["maxLength"] == 512
    assert payload["properties"]["sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert set(schemas["PackUploadMode"]["enum"]) == {
        "SINGLE_PUT",
        "MULTIPART",
    }
    assert (
        schemas["PackBatchAck"]["properties"]["ack_cursor"]["minimum"]
        == 0
    )
    assert (
        schemas["PackMultipartState"]["properties"]["uploaded_parts"][
            "maxItems"
        ]
        == 10000
    )
    assert set(schemas["PackImportStatus"]["enum"]) == {
        "PENDING_VALIDATION",
        "VALIDATING",
        "IMPORTED",
        "IMPORTED_PARTIAL",
        "QUARANTINED",
        "REJECTED",
    }
    forbidden = {
        "prompt",
        "messages",
        "completion",
        "tool_arguments",
        "tool_result",
        "payload",
        "content",
    }
    assert forbidden.isdisjoint(manifest["properties"])
    assert forbidden.isdisjoint(import_state["properties"])


def test_adapter_fleet_contract_is_dynamic_metadata_only_and_opaque() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]
    for path in (
        "/api/v2/reporter/handshakes",
        "/api/v2/reporter/heartbeats",
    ):
        assert _operation(spec, path, "post")["security"] == [
            {"ReporterBearerAuth": []}
        ]
    fleet_operation = _operation(
        spec,
        "/api/v2/fleet/runtimes",
        "get",
    )
    assert fleet_operation["security"] == [{"UserBearerAuth": []}]
    descriptor = schemas["AdapterDescriptor"]
    runtime = schemas["FleetRuntime"]
    assert descriptor["additionalProperties"] is False
    assert runtime["additionalProperties"] is False
    assert descriptor["properties"]["client_nonce"]["minLength"] == 22
    assert runtime["properties"]["runtime_public_id"]["pattern"] == (
        "^rt_[0-9a-f]{32}$"
    )
    assert {"namespace_id", "runtime_id", "reporter_credential_id"}.isdisjoint(
        runtime["properties"]
    )
    assert runtime["properties"]["heartbeat_history"]["maxItems"] == 20


def test_artifact_and_telemetry_contracts_are_metadata_and_reference_only() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]
    artifact_create = schemas["AgentRunArtifactCreate"]
    sink_create = schemas["TelemetrySinkCreate"]
    sink_config = schemas["TelemetrySinkConfig"]

    assert artifact_create["additionalProperties"] is False
    assert {"object_uri", "sha256", "size_bytes"} <= set(
        artifact_create["required"]
    )
    assert {
        "prompt",
        "completion",
        "payload",
        "content",
        "tool_arguments",
    }.isdisjoint(artifact_create["properties"])

    assert sink_create["additionalProperties"] is False
    assert sink_config["additionalProperties"] is False
    assert "credential_ref" in sink_create["required"]
    assert {"api_key", "token", "password", "secret"}.isdisjoint(
        set(sink_create["properties"]) | set(sink_config["properties"])
    )


def test_outbox_operations_never_expose_event_payload_or_error_body() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]
    event = schemas["OutboxEvent"]
    health = schemas["OutboxHealth"]

    assert event["additionalProperties"] is False
    assert {"payload", "payload_json", "last_error"}.isdisjoint(
        event["properties"]
    )
    assert "event_id" in event["required"]
    assert health["additionalProperties"] is False
    assert {
        "pending_count",
        "failed_count",
        "oldest_pending_age_seconds",
    } <= set(health["required"])


def test_reconciliation_contract_explains_legacy_only_without_raw_content() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]
    reconciliation = schemas["V1V2Reconciliation"]
    health = schemas["ReconciliationHealth"]

    assert schemas["ReconciliationStatus"]["enum"] == [
        "MATCHED",
        "EXPECTED_LEGACY_ONLY",
        "MISMATCH",
    ]
    assert reconciliation["additionalProperties"] is False
    assert {
        "event_id",
        "last_event_id",
        "payload",
        "payload_json",
        "title",
        "summary",
        "highlights",
    }.isdisjoint(reconciliation["properties"])
    assert health["additionalProperties"] is False
    assert health["properties"]["unexplained_difference_count"] == {
        "type": "integer",
        "minimum": 0,
    }


def test_reporter_mutations_document_conflict_not_found_and_validation_errors() -> None:
    spec = _load_openapi()
    expected_errors = {"404", "409", "422"}

    for path in REPORTER_PATHS:
        responses = _operation(spec, path, "post")["responses"]
        assert expected_errors <= set(responses), path
        for status in expected_errors:
            response = responses[status]
            assert response["$ref"] == {
                "404": "#/components/responses/NotFound",
                "409": "#/components/responses/Conflict",
                "422": "#/components/responses/ValidationError",
            }[status]


def test_agent_run_list_and_detail_responses_are_typed() -> None:
    spec = _load_openapi()
    list_operation = _operation(spec, "/api/v2/agent-runs", "get")
    detail_operation = _operation(spec, "/api/v2/agent-runs/{run_public_id}", "get")

    assert "200" in list_operation["responses"]
    assert "422" in list_operation["responses"]
    assert "200" in detail_operation["responses"]
    assert "404" in detail_operation["responses"]
    assert "422" in detail_operation["responses"]

    list_schema = list_operation["responses"]["200"]["content"]["application/json"]["schema"]
    detail_schema = detail_operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert list_schema["$ref"] == "#/components/schemas/AgentRunList"
    assert detail_schema["$ref"] == "#/components/schemas/AgentRun"


def test_agent_run_list_requires_bounded_time_parameters() -> None:
    spec = _load_openapi()
    operation = _operation(spec, "/api/v2/agent-runs", "get")
    parameter_refs = {
        parameter["$ref"]
        for parameter in operation["parameters"]
        if "$ref" in parameter
    }

    assert "#/components/parameters/StartedAfter" in parameter_refs
    assert "#/components/parameters/StartedBefore" in parameter_refs
    assert spec["components"]["parameters"]["StartedAfter"]["required"] is True
    assert spec["components"]["parameters"]["StartedBefore"]["required"] is True


def test_reporter_execution_scope_is_explicit_in_the_contract() -> None:
    spec = _load_openapi()
    reporter_auth = spec["components"]["securitySchemes"][
        "ReporterBearerAuth"
    ]

    assert "execution.write" in reporter_auth["description"]
    for path in REPORTER_PATHS:
        responses = _operation(spec, path, "post")["responses"]
        assert responses["403"]["$ref"] == "#/components/responses/Forbidden"


def test_reporter_starts_document_rollout_backout_response() -> None:
    spec = _load_openapi()

    for path in REPORTER_START_PATHS:
        responses = _operation(spec, path, "post")["responses"]
        assert responses["503"]["$ref"] == (
            "#/components/responses/IngestionDisabled"
        )
    for path in set(REPORTER_PATHS) - REPORTER_START_PATHS:
        assert "503" not in _operation(spec, path, "post")["responses"]

    disabled = spec["components"]["responses"]["IngestionDisabled"]
    assert "Retry-After" in disabled["headers"]


def test_openapi_reuses_the_four_foundation_contracts() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]
    external_contracts = {
        "AgentPackageV2": "./agent-package-v2.schema.json",
        "TelemetryEnvelopeV1": "./telemetry-envelope-v1.schema.json",
        "EvaluationResultV1": "./evaluation-result-v1.schema.json",
        "ReleaseManifestV1": "./release-manifest-v1.schema.json",
    }

    for component_name, expected_ref in external_contracts.items():
        assert schemas[component_name] == {"$ref": expected_ref}
        contract_path = CONTRACTS_DIR / expected_ref.removeprefix("./")
        with contract_path.open(encoding="utf-8") as handle:
            contract = json.load(handle)
        assert contract["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    agent_run = schemas["AgentRun"]
    assert agent_run["properties"]["telemetry_summary"]["$ref"] == (
        "#/components/schemas/MetadataOnlyTelemetryEnvelope"
    )

    metadata_telemetry = schemas["MetadataOnlyTelemetryEnvelope"]
    assert metadata_telemetry["allOf"][0]["$ref"] == "#/components/schemas/TelemetryEnvelopeV1"
    assert metadata_telemetry["allOf"][1]["properties"]["content_policy"]["$ref"] == (
        "#/components/schemas/MetadataOnlyContentPolicy"
    )


def test_metadata_only_is_the_v2_ingestion_default_and_contract() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]
    capture_mode = schemas["MetadataOnlyCaptureMode"]

    assert capture_mode["type"] == "string"
    assert capture_mode["const"] == "metadata_only"
    for schema_name in ("SessionStartRequest", "RunStartRequest", "AgentRun"):
        schema = schemas[schema_name]
        assert "content_capture_mode" in schema["required"]
        assert schema["properties"]["content_capture_mode"]["$ref"] == (
            "#/components/schemas/MetadataOnlyCaptureMode"
        )


def test_trust_strength_is_separate_from_ingress_source() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]

    assert schemas["TrustLevel"]["enum"] == [
        "CHANNEL_AUTHENTICATED",
        "PRODUCER_ATTESTED",
        "UNVERIFIED",
    ]
    assert schemas["TrustSource"]["enum"] == [
        "REPORTER",
        "COLLECTOR",
        "IMPORT",
        "ADMIN",
    ]
    for schema_name in ("RunStartResponse", "AgentRunSummary", "AgentRun"):
        schema = schemas[schema_name]
        assert {"trust_level", "trust_source"} <= set(schema["required"])
        assert schema["properties"]["trust_level"]["$ref"] == (
            "#/components/schemas/TrustLevel"
        )
        assert schema["properties"]["trust_source"]["$ref"] == (
            "#/components/schemas/TrustSource"
        )


def test_reporter_write_contract_derives_tenant_and_runtime_from_credential() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]
    forbidden_identity_fields = {"namespace_id", "runtime_id", "runtime_instance_id"}

    for schema_name in (
        "SessionStartRequest",
        "SessionCompleteRequest",
        "RunStartRequest",
        "RunCompleteRequest",
    ):
        properties = set(schemas[schema_name]["properties"])
        assert properties.isdisjoint(forbidden_identity_fields), schema_name


def test_reporter_completion_does_not_accept_eval_or_full_telemetry_payloads() -> None:
    spec = _load_openapi()
    properties = set(spec["components"]["schemas"]["RunCompleteRequest"]["properties"])

    assert properties.isdisjoint(
        {
            "telemetry",
            "telemetry_summary",
            "evaluation_result",
            "prompt",
            "completion",
            "messages",
            "tool_arguments",
            "tool_result",
        }
    )


def test_metadata_keys_cannot_alias_forbidden_content_fields() -> None:
    spec = _load_openapi()
    metadata = spec["components"]["schemas"]["MetadataAttributes"]
    allowed_keys = set(metadata["propertyNames"]["enum"])

    assert "deployment.environment" in allowed_keys
    for forbidden_key in (
        "prompt",
        "request.prompt_text",
        "model_input",
        "model_output",
        "retrieved_content",
        "tool_arguments",
        "tool-result",
        "chain_of_thought",
        "shell_output",
        "span_events",
        "stack_trace",
        "file_body",
        "screenshot",
        "audio",
        "base64",
        "authorization",
        "api_secret",
    ):
        assert forbidden_key not in allowed_keys


def test_problem_and_core_lifecycle_schemas_are_strict() -> None:
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]

    problem = schemas["Problem"]
    assert problem["additionalProperties"] is False
    assert {"status", "code", "detail", "request_id", "retryable"} <= set(
        problem["required"]
    )
    assert problem["properties"]["retryable"] == {"type": "boolean"}
    assert problem["properties"]["details"]["type"] == "object"

    expected_required = {
        "SessionStartRequest": {
            "external_session_id",
            "started_at",
            "content_capture_mode",
        },
        "SessionCompleteRequest": {"ended_at", "status"},
        "RunStartRequest": {
            "external_run_id",
            "started_at",
            "source_schema",
            "source_schema_version",
            "content_capture_mode",
        },
        "RunCompleteRequest": {"ended_at", "status"},
        "AgentRun": {
            "run_public_id",
            "namespace_id",
            "runtime_instance_id",
            "external_run_id",
            "status",
            "trust_level",
            "trust_source",
            "source_schema",
            "source_schema_version",
            "started_at",
            "content_capture_mode",
        },
    }
    for schema_name, required in expected_required.items():
        schema = schemas[schema_name]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert required <= set(schema["required"])


def test_reusable_error_responses_return_problem_json() -> None:
    spec = _load_openapi()

    for response_name in ("Unauthorized", "NotFound", "Conflict", "ValidationError"):
        response = spec["components"]["responses"][response_name]
        schema = response["content"]["application/problem+json"]["schema"]
        assert schema["$ref"] == "#/components/schemas/Problem"


def test_quickstart_reporter_examples_match_openapi_requests() -> None:
    spec = _load_openapi()
    text = QUICKSTART_PATH.read_text(encoding="utf-8")
    blocks = re.findall(r"```http\s*\n(.*?)```", text, flags=re.DOTALL)
    examples: dict[str, dict[str, Any]] = {}

    for block in blocks:
        request_line = block.splitlines()[0].strip()
        method, path = request_line.split(maxsplit=1)
        assert method == "POST"
        body_start = block.find("\n{") + 1
        assert body_start > 0, path
        examples[path] = json.loads(block[body_start:].strip())

    assert set(examples) == set(REPORTER_PATHS)
    for path, body in examples.items():
        operation = _operation(spec, path, "post")
        request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
        _validate_openapi_example(body, request_schema, spec)
