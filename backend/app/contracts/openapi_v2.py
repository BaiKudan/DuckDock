from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from fastapi import FastAPI

from app.version import DUCKDOCK_VERSION


CONTRACT_VERSION = DUCKDOCK_VERSION
# Updated only after the generated snapshot and the live schema match.
EXPECTED_CONTRACT_SHA256 = "aa260f301acc5c3a8004d14980952a03ce0197f9d70dcdd78cd62e986c3b1a83"


def _iter_component_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/"):
            yield ref
        for nested in value.values():
            yield from _iter_component_refs(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_component_refs(nested)


def _component_key(ref: str) -> tuple[str, str] | None:
    parts = ref.split("/")
    if len(parts) != 4 or parts[:2] != ["#", "components"]:
        return None
    return parts[2], parts[3]


def _security_scheme_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        security = value.get("security")
        if isinstance(security, list):
            for requirement in security:
                if isinstance(requirement, dict):
                    names.update(str(name) for name in requirement)
        for nested in value.values():
            names.update(_security_scheme_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.update(_security_scheme_names(nested))
    return names


def _reachable_components(source: dict[str, Any], paths: dict[str, Any]) -> dict[str, Any]:
    """Keep only components reachable from v2 operations.

    FastAPI builds one application-wide component registry. Copying that registry
    into the v2 snapshot leaked every v1-only schema into the public v2 contract.
    """

    source_components = source.get("components", {})
    pending = list(_iter_component_refs(paths))
    reachable: set[tuple[str, str]] = set()
    while pending:
        key = _component_key(pending.pop())
        if key is None or key in reachable:
            continue
        section, name = key
        component = source_components.get(section, {}).get(name)
        if component is None:
            raise ValueError(f"OpenAPI component reference is unresolved: #/components/{section}/{name}")
        reachable.add(key)
        pending.extend(_iter_component_refs(component))

    for name in _security_scheme_names(paths):
        if name not in source_components.get("securitySchemes", {}):
            raise ValueError(f"OpenAPI security scheme is unresolved: {name}")
        reachable.add(("securitySchemes", name))

    components: dict[str, dict[str, Any]] = {}
    for section, name in sorted(reachable):
        components.setdefault(section, {})[name] = deepcopy(source_components[section][name])
    return components


def build_openapi_v2_contract(app: FastAPI) -> dict[str, Any]:
    """Build a deterministic, self-contained contract containing only v2 paths."""

    source = app.openapi()
    contract = deepcopy(source)
    contract["info"] = {
        **contract.get("info", {}),
        "title": "DuckDock API v2",
        "version": CONTRACT_VERSION,
        "description": (
            "Versioned DuckDock 2.0 API contract. The components section is kept "
            "self-contained so every v2 schema reference remains resolvable."
        ),
    }
    v2_paths = {
        path: value
        for path, value in sorted(contract.get("paths", {}).items())
        if path.startswith("/api/v2")
    }
    contract["paths"] = v2_paths
    contract["components"] = _reachable_components(source, v2_paths)
    return contract


def render_openapi_v2_contract(app: FastAPI) -> bytes:
    return (
        json.dumps(
            build_openapi_v2_contract(app),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def openapi_v2_contract_digest(app: FastAPI) -> str:
    return hashlib.sha256(render_openapi_v2_contract(app)).hexdigest()
