from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = (
    PROJECT_ROOT
    / "specs"
    / "009-foundation-tenant-contract"
    / "contracts"
    / "tenant-remediation-manifest-v1.schema.json"
)


def test_manifest_contract_is_strict_versioned_and_has_no_bulk_selector():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assignment = schema["$defs"]["assignment"]
    assert assignment["additionalProperties"] is False
    assert set(assignment["required"]) == {"target_type", "target_id", "namespace_id"}
    assert set(assignment["properties"]) == {
        "target_type",
        "target_id",
        "namespace_id",
        "reason",
    }
    serialized = json.dumps(schema, sort_keys=True).lower()
    assert "default_namespace" not in serialized
    assert "selector" not in serialized
    assert "wildcard" not in serialized
