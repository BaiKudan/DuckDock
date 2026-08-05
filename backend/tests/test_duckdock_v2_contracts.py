from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = PROJECT_ROOT / "specs" / "008-duckdock-2-foundation" / "contracts"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"

CONTRACT_NAMES = (
    "agent-package-v2",
    "telemetry-envelope-v1",
    "evaluation-result-v1",
    "release-manifest-v1",
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    assert isinstance(value, dict), f"{path} must contain a JSON object"
    return value


def _resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    assert ref.startswith("#/"), f"only local JSON Schema refs are supported in tests: {ref}"
    current: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    assert isinstance(current, dict), f"JSON Schema ref must resolve to an object: {ref}"
    return current


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise AssertionError(f"unsupported JSON Schema type in contract test: {expected}")


def _validate_format(value: str, format_name: str, path: str) -> None:
    if format_name == "uuid":
        try:
            uuid.UUID(value)
        except ValueError as exc:
            raise AssertionError(f"{path} is not a UUID: {value}") from exc
        return

    if format_name == "date-time":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AssertionError(f"{path} is not an ISO-8601 date-time: {value}") from exc
        assert parsed.tzinfo is not None, f"{path} date-time must include a timezone"
        return

    if format_name == "uri":
        assert urlsplit(value).scheme, f"{path} is not an absolute URI: {value}"
        return

    raise AssertionError(f"unsupported JSON Schema format in contract test: {format_name}")


def _validate(
    value: Any,
    schema: dict[str, Any] | bool,
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    if schema is True:
        return
    if schema is False:
        raise AssertionError(f"{path} is forbidden by the contract")

    if "$ref" in schema:
        _validate(value, _resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    if "allOf" in schema:
        for candidate in schema["allOf"]:
            _validate(value, candidate, root_schema, path)

    if "not" in schema:
        try:
            _validate(value, schema["not"], root_schema, path)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"{path} matches a forbidden contract branch")

    if "if" in schema:
        try:
            _validate(value, schema["if"], root_schema, path)
        except AssertionError:
            if "else" in schema:
                _validate(value, schema["else"], root_schema, path)
        else:
            if "then" in schema:
                _validate(value, schema["then"], root_schema, path)

    if "oneOf" in schema:
        successful = 0
        for candidate in schema["oneOf"]:
            try:
                _validate(value, candidate, root_schema, path)
            except AssertionError:
                continue
            successful += 1
        assert successful == 1, f"{path} must match exactly one oneOf branch, matched {successful}"
        return

    if "const" in schema:
        assert value == schema["const"], f"{path} must equal {schema['const']!r}"

    if "enum" in schema:
        assert value in schema["enum"], f"{path} is not in {schema['enum']!r}"

    declared_type = schema.get("type")
    if declared_type is not None:
        expected_types = declared_type if isinstance(declared_type, list) else [declared_type]
        assert any(_matches_type(value, expected) for expected in expected_types), (
            f"{path} expected type {expected_types!r}, got {type(value).__name__}"
        )

    if value is None:
        return

    if isinstance(value, dict):
        if "maxProperties" in schema:
            assert len(value) <= schema["maxProperties"], f"{path} has too many properties"
        if "propertyNames" in schema:
            for key in value:
                _validate(key, schema["propertyNames"], root_schema, f"{path}.<key>")

        required = schema.get("required", [])
        missing = [field for field in required if field not in value]
        assert not missing, f"{path} is missing required fields: {missing}"

        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                _validate(child, properties[key], root_schema, f"{path}.{key}")
                continue

            additional = schema.get("additionalProperties", True)
            assert additional is not False, f"{path} contains unsupported field: {key}"
            if isinstance(additional, dict):
                _validate(child, additional, root_schema, f"{path}.{key}")

    if isinstance(value, list):
        assert len(value) >= schema.get("minItems", 0), f"{path} has too few items"
        if "maxItems" in schema:
            assert len(value) <= schema["maxItems"], f"{path} has too many items"
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            assert len(canonical) == len(set(canonical)), f"{path} must contain unique items"
        if "items" in schema:
            for index, item in enumerate(value):
                _validate(item, schema["items"], root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        assert len(value) >= schema.get("minLength", 0), f"{path} is too short"
        if "maxLength" in schema:
            assert len(value) <= schema["maxLength"], f"{path} is too long"
        if "pattern" in schema:
            assert re.search(schema["pattern"], value), f"{path} does not match {schema['pattern']}"
        if "format" in schema:
            _validate_format(value, schema["format"], path)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            assert value >= schema["minimum"], f"{path} is below its minimum"
        if "maximum" in schema:
            assert value <= schema["maximum"], f"{path} is above its maximum"


def _expect_invalid(value: Any, schema: dict[str, Any]) -> None:
    try:
        _validate(value, schema, schema)
    except AssertionError:
        return
    raise AssertionError("contract value unexpectedly passed validation")


def test_contract_schema_and_example_sets_match() -> None:
    schema_names = {
        path.name.removesuffix(".schema.json")
        for path in CONTRACTS_DIR.glob("*.schema.json")
    }
    example_names = {
        path.name.removesuffix(".example.json")
        for path in EXAMPLES_DIR.glob("*.example.json")
    }

    assert schema_names == set(CONTRACT_NAMES)
    assert example_names == set(CONTRACT_NAMES)


def test_contract_schemas_are_versioned_strict_root_objects() -> None:
    expected_versions = {
        "agent-package-v2": "2.0",
        "telemetry-envelope-v1": "1.0",
        "evaluation-result-v1": "1.0",
        "release-manifest-v1": "1.0",
    }

    for name in CONTRACT_NAMES:
        schema = _load_json(CONTRACTS_DIR / f"{name}.schema.json")
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/{name}.schema.json")
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["schema_version"]["const"] == expected_versions[name]
        assert "schema_version" in schema["required"]
        assert len(schema["required"]) == len(set(schema["required"]))


def test_all_examples_satisfy_their_contracts_without_external_validator() -> None:
    for name in CONTRACT_NAMES:
        schema = _load_json(CONTRACTS_DIR / f"{name}.schema.json")
        example = _load_json(EXAMPLES_DIR / f"{name}.example.json")
        _validate(example, schema, schema)


def test_required_and_unknown_fields_are_rejected() -> None:
    for name in CONTRACT_NAMES:
        schema = _load_json(CONTRACTS_DIR / f"{name}.schema.json")
        example = _load_json(EXAMPLES_DIR / f"{name}.example.json")

        for required_field in schema["required"]:
            invalid = copy.deepcopy(example)
            del invalid[required_field]
            _expect_invalid(invalid, schema)

        invalid = copy.deepcopy(example)
        invalid["unsupported_contract_field"] = True
        _expect_invalid(invalid, schema)


def test_digest_trace_id_and_signature_shapes_are_enforced() -> None:
    package_schema = _load_json(CONTRACTS_DIR / "agent-package-v2.schema.json")
    package = _load_json(EXAMPLES_DIR / "agent-package-v2.example.json")
    invalid_package = copy.deepcopy(package)
    invalid_package["artifacts"][0]["sha256"] = "not-a-sha256"
    _expect_invalid(invalid_package, package_schema)

    telemetry_schema = _load_json(CONTRACTS_DIR / "telemetry-envelope-v1.schema.json")
    telemetry = _load_json(EXAMPLES_DIR / "telemetry-envelope-v1.example.json")
    invalid_telemetry = copy.deepcopy(telemetry)
    invalid_telemetry["trace"]["trace_id"] = "short"
    _expect_invalid(invalid_telemetry, telemetry_schema)

    release_schema = _load_json(CONTRACTS_DIR / "release-manifest-v1.schema.json")
    release = _load_json(EXAMPLES_DIR / "release-manifest-v1.example.json")
    invalid_release = copy.deepcopy(release)
    invalid_release["signature"]["value"] = "invalid signature with spaces"
    _expect_invalid(invalid_release, release_schema)


def test_examples_form_one_consistent_release_evidence_chain() -> None:
    package = _load_json(EXAMPLES_DIR / "agent-package-v2.example.json")
    telemetry = _load_json(EXAMPLES_DIR / "telemetry-envelope-v1.example.json")
    evaluation = _load_json(EXAMPLES_DIR / "evaluation-result-v1.example.json")
    release = _load_json(EXAMPLES_DIR / "release-manifest-v1.example.json")

    assert package["namespace_id"] == telemetry["correlation"]["namespace_id"]
    assert package["namespace_id"] == evaluation["subject"]["namespace_id"]
    assert package["namespace_id"] == release["namespace_id"]

    assert package["agent"]["asset_id"] == telemetry["correlation"]["agent_asset_id"]
    assert package["agent"]["asset_id"] == evaluation["subject"]["agent_asset_id"]

    assert package["package_id"] == evaluation["subject"]["package_id"]
    assert package["package_id"] == release["package"]["package_id"]
    assert package["package_version"] == release["package"]["package_version"]

    assert telemetry["correlation"]["release_id"] == evaluation["subject"]["release_id"]
    assert telemetry["correlation"]["release_id"] == release["release_id"]
    assert telemetry["correlation"]["runtime_instance_id"] in release["target"]["runtime_instance_ids"]
    assert telemetry["provider"]["external_trace_id"] == telemetry["trace"]["trace_id"]
    assert package["telemetry"]["content_policy"] == telemetry["content_policy"]

    assert package["evaluation_policy_id"] == release["quality_gate"]["policy_id"]
    assert evaluation["result_id"] in release["quality_gate"]["evaluation_result_ids"]
    assert package["provenance"]["source_revision"] == evaluation["provenance"]["source_revision"]
    assert package["provenance"]["source_revision"] == release["provenance"]["source_revision"]
    assert package["provenance"]["build_id"] == release["provenance"]["build_id"]


def test_evaluation_example_has_internally_consistent_counts_and_decision() -> None:
    evaluation = _load_json(EXAMPLES_DIR / "evaluation-result-v1.example.json")
    summary = evaluation["summary"]

    assert summary["items_total"] == evaluation["dataset"]["item_count"]
    assert summary["items_evaluated"] == summary["items_passed"] + summary["items_failed"]
    assert summary["items_total"] == summary["items_evaluated"] + summary["items_error"]

    for metric in evaluation["metrics"]:
        if metric["comparison"] == "gte":
            expected_passed = metric["score"] >= metric["threshold"]
        elif metric["comparison"] == "lte":
            expected_passed = metric["score"] <= metric["threshold"]
        else:
            expected_passed = metric["score"] == metric["threshold"]
        assert metric["passed"] is expected_passed

    assert evaluation["decision"]["outcome"] == "pass"
    assert not evaluation["decision"]["blocking_reasons"]
    assert all(metric["passed"] for metric in evaluation["metrics"])


def test_release_example_requires_human_approval_and_rollback_evidence() -> None:
    release = _load_json(EXAMPLES_DIR / "release-manifest-v1.example.json")

    assert release["quality_gate"]["decision"] == "pass"
    assert release["approvals"]
    assert all(approval["decision"] == "approved" for approval in release["approvals"])
    assert release["rollback"]["reversible"] is True
    assert release["rollback"]["previous_release_id"]


def test_release_waiver_requires_a_reason_and_expiry() -> None:
    schema = _load_json(CONTRACTS_DIR / "release-manifest-v1.schema.json")
    release = _load_json(EXAMPLES_DIR / "release-manifest-v1.example.json")

    invalid_waiver = copy.deepcopy(release)
    invalid_waiver["quality_gate"]["decision"] = "waived"
    _expect_invalid(invalid_waiver, schema)

    invalid_pass = copy.deepcopy(release)
    invalid_pass["quality_gate"]["waiver_reason"] = "not applicable to a passing gate"
    invalid_pass["quality_gate"]["waiver_expires_at"] = "2026-08-01T00:00:00Z"
    _expect_invalid(invalid_pass, schema)


def test_evaluation_result_represents_terminal_completed_evidence_only() -> None:
    schema = _load_json(CONTRACTS_DIR / "evaluation-result-v1.schema.json")
    evaluation = _load_json(EXAMPLES_DIR / "evaluation-result-v1.example.json")

    invalid_running_result = copy.deepcopy(evaluation)
    invalid_running_result["status"] = "running"
    _expect_invalid(invalid_running_result, schema)


def test_metadata_only_telemetry_example_contains_no_raw_content_fields() -> None:
    telemetry = _load_json(EXAMPLES_DIR / "telemetry-envelope-v1.example.json")
    serialized_keys = {key.lower() for key in _walk_keys(telemetry)}

    assert telemetry["content_policy"] == "metadata_only"
    assert serialized_keys.isdisjoint({"prompt", "completion", "input", "output", "tool_arguments"})


def test_free_form_metadata_uses_explicit_key_allowlists() -> None:
    package_schema = _load_json(CONTRACTS_DIR / "agent-package-v2.schema.json")
    package = _load_json(EXAMPLES_DIR / "agent-package-v2.example.json")
    invalid_package = copy.deepcopy(package)
    invalid_package["annotations"]["prompt"] = "must never be accepted as an annotation"
    _expect_invalid(invalid_package, package_schema)

    telemetry_schema = _load_json(CONTRACTS_DIR / "telemetry-envelope-v1.schema.json")
    telemetry = _load_json(EXAMPLES_DIR / "telemetry-envelope-v1.example.json")
    invalid_telemetry = copy.deepcopy(telemetry)
    invalid_telemetry["attributes"]["model_input"] = "must never be accepted as metadata"
    _expect_invalid(invalid_telemetry, telemetry_schema)


def test_telemetry_metadata_is_bounded_and_required_attributes_are_safe() -> None:
    package_schema = _load_json(CONTRACTS_DIR / "agent-package-v2.schema.json")
    package = _load_json(EXAMPLES_DIR / "agent-package-v2.example.json")
    unsafe_package = copy.deepcopy(package)
    unsafe_package["telemetry"]["required_attributes"].append("prompt")
    _expect_invalid(unsafe_package, package_schema)

    telemetry_schema = _load_json(CONTRACTS_DIR / "telemetry-envelope-v1.schema.json")
    telemetry = _load_json(EXAMPLES_DIR / "telemetry-envelope-v1.example.json")

    oversized_string = copy.deepcopy(telemetry)
    oversized_string["attributes"]["service.name"] = "x" * 2001
    _expect_invalid(oversized_string, telemetry_schema)

    oversized_array = copy.deepcopy(telemetry)
    oversized_array["attributes"]["duckdock.asset_version_ids"] = [
        f"asset-{index}" for index in range(101)
    ]
    _expect_invalid(oversized_array, telemetry_schema)

    oversized_array_item = copy.deepcopy(telemetry)
    oversized_array_item["attributes"]["duckdock.asset_version_ids"] = ["x" * 501]
    _expect_invalid(oversized_array_item, telemetry_schema)


def test_package_component_graph_and_sbom_contracts_are_strict() -> None:
    schema = _load_json(CONTRACTS_DIR / "agent-package-v2.schema.json")
    package = _load_json(EXAMPLES_DIR / "agent-package-v2.example.json")

    assert package["component_graph"]["edges"]
    assert package["sbom"]["format"] == "cyclonedx-json"

    unsafe_edge = copy.deepcopy(package)
    unsafe_edge["component_graph"]["edges"][0]["prompt"] = "forbidden"
    _expect_invalid(unsafe_edge, schema)

    bad_sbom_digest = copy.deepcopy(package)
    bad_sbom_digest["sbom"]["document_sha256"] = "not-a-digest"
    _expect_invalid(bad_sbom_digest, schema)


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            keys.append(key)
            keys.extend(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(_walk_keys(child))
    return keys
