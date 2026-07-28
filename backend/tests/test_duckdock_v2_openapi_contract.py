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
AGENT_RUN_PATHS = {
    "/api/v2/agent-runs": "listAgentRuns",
    "/api/v2/agent-runs/{run_public_id}": "getAgentRun",
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
    assert set(REPORTER_PATHS) | set(AGENT_RUN_PATHS) <= set(spec["paths"])
    assert all(not path.startswith("/api/v1") for path in spec["paths"])

    operation_ids: list[str] = []
    for path, expected_operation_id in REPORTER_PATHS.items():
        operation = _operation(spec, path, "post")
        assert operation["operationId"] == expected_operation_id
        operation_ids.append(operation["operationId"])
    for path, expected_operation_id in AGENT_RUN_PATHS.items():
        operation = _operation(spec, path, "get")
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
