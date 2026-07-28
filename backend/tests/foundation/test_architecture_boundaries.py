from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"
MODELS_ROOT = APP_ROOT / "models"
CORE_IMPORT_ROOTS = (APP_ROOT / "services", APP_ROOT / "ports")

VENDOR_SDK_ROOTS = frozenset(
    {
        "agentloop",
        "atif",
        "deepeval",
        "harbor",
        "harbor_framework",
        "langfuse",
    }
)

# These names represent execution content, not governance metadata. Digest,
# version, count, media/content type, and object references remain valid model
# fields because they do not copy the underlying prompt/span/tool payload.
FORBIDDEN_RAW_FIELD_NAMES = frozenset(
    {
        "completion",
        "completion_text",
        "content",
        "content_json",
        "content_text",
        "conversation",
        "conversation_json",
        "message_history",
        "messages",
        "prompt",
        "prompt_json",
        "prompt_text",
        "raw_content",
        "raw_payload",
        "raw_prompt",
        "raw_span",
        "raw_trace",
        "request_body",
        "response",
        "response_body",
        "response_text",
        "span_events",
        "span_json",
        "system_prompt",
        "tool_arguments",
        "tool_event",
        "tool_events",
        "tool_input",
        "tool_output",
        "tool_result",
        "tool_results",
    }
)


@dataclass(frozen=True, order=True)
class BoundaryViolation:
    rule: str
    path: str
    line: int
    symbol: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rule, self.path, self.symbol)

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.symbol}"


# Existing v1 debt is pinned narrowly so the guard prevents its spread without
# claiming that an architecture-only test migrated legacy business storage.
# Removing the legacy field must also remove this exception.
LEGACY_MODEL_EXCEPTIONS = frozenset(
    {
        (
            "RAW_CONTENT_FIELD",
            "app/models/webhook.py",
            "WebhookDelivery.response_body",
        ),
    }
)

# The legacy Langfuse integration is already isolated in one optional module
# and catches an absent SDK at import time. New DuckDock 2.0 core code must use
# a Port/Adapter instead of expanding this exception.
LEGACY_VENDOR_IMPORT_EXCEPTIONS = frozenset(
    {
        (
            "VENDOR_SDK_IMPORT",
            "app/services/langfuse_service.py",
            "langfuse",
        ),
    }
)


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _parse_python(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _identifier_tokens(value: str) -> set[str]:
    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return {token for token in re.split(r"[^a-z0-9]+", snake_case) if token}


def _is_forbidden_execution_model(class_name: str, table_name: str) -> bool:
    tokens = _identifier_tokens(class_name) | _identifier_tokens(table_name)
    if tokens & {"span", "spans", "prompt", "prompts"}:
        return True
    if "tool" in tokens and ("event" in tokens or "events" in tokens):
        return True
    if "raw" in tokens and tokens & {
        "content",
        "contents",
        "payload",
        "payloads",
        "prompt",
        "prompts",
        "response",
        "responses",
        "span",
        "spans",
        "tool",
        "tools",
        "trace",
        "traces",
    }:
        return True
    return False


def _class_table_name(class_node: ast.ClassDef) -> tuple[str | None, int]:
    for statement in class_node.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__tablename__" for target in statement.targets):
            continue
        if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            return statement.value.value, statement.lineno
    return None, class_node.lineno


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _orm_column_names(statement: ast.stmt) -> set[str]:
    target_name: str | None = None
    value: ast.expr | None = None

    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        target_name = statement.target.id
        value = statement.value
    elif isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
        target_name = statement.targets[0].id
        value = statement.value

    if target_name is None or value is None:
        return set()

    column_calls = [
        node
        for node in ast.walk(value)
        if isinstance(node, ast.Call) and _call_name(node) in {"Column", "mapped_column"}
    ]
    if not column_calls:
        return set()

    names = {target_name}
    for call in column_calls:
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
            names.add(call.args[0].value)
    return names


def _scan_model_boundaries(models_root: Path, *, project_root: Path) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    for path in _python_files(models_root):
        display_path = _display_path(path, project_root)
        tree = _parse_python(path)
        for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            table_name, table_line = _class_table_name(class_node)
            if table_name and _is_forbidden_execution_model(class_node.name, table_name):
                violations.append(
                    BoundaryViolation(
                        rule="RAW_EXECUTION_MODEL",
                        path=display_path,
                        line=table_line,
                        symbol=f"{class_node.name}.__tablename__={table_name}",
                    )
                )

            for statement in class_node.body:
                for column_name in _orm_column_names(statement):
                    if column_name.lower() not in FORBIDDEN_RAW_FIELD_NAMES:
                        continue
                    violations.append(
                        BoundaryViolation(
                            rule="RAW_CONTENT_FIELD",
                            path=display_path,
                            line=statement.lineno,
                            symbol=f"{class_node.name}.{column_name}",
                        )
                    )
    return sorted(set(violations))


def _vendor_root(module_name: str | None) -> str | None:
    if not module_name:
        return None
    return module_name.split(".", maxsplit=1)[0]


def _dynamic_import_name(node: ast.Call) -> str | None:
    is_import_call = isinstance(node.func, ast.Name) and node.func.id in {"__import__", "import_module"}
    is_importlib_call = (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
        and node.func.attr == "import_module"
    )
    if not (is_import_call or is_importlib_call) or not node.args:
        return None
    first_arg = node.args[0]
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        return first_arg.value
    return None


def _scan_vendor_imports(import_roots: tuple[Path, ...], *, project_root: Path) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    paths = sorted({path for root in import_roots for path in _python_files(root)})
    for path in paths:
        display_path = _display_path(path, project_root)
        tree = _parse_python(path)
        for node in ast.walk(tree):
            imported_names: list[str] = []
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.append(node.module)
            elif isinstance(node, ast.Call):
                dynamic_name = _dynamic_import_name(node)
                if dynamic_name:
                    imported_names.append(dynamic_name)

            for imported_name in imported_names:
                root = _vendor_root(imported_name)
                if root not in VENDOR_SDK_ROOTS:
                    continue
                violations.append(
                    BoundaryViolation(
                        rule="VENDOR_SDK_IMPORT",
                        path=display_path,
                        line=node.lineno,
                        symbol=root,
                    )
                )
    return sorted(set(violations))


def _violation_report(violations: list[BoundaryViolation]) -> str:
    return "\n".join(violation.render() for violation in violations)


def test_boundary_scanner_detects_forbidden_model_fixture(tmp_path: Path) -> None:
    models_root = tmp_path / "app" / "models"
    models_root.mkdir(parents=True)
    (models_root / "bad_telemetry.py").write_text(
        textwrap.dedent(
            """
            class SpanRecord(Base):
                __tablename__ = "agent_spans"
                id = mapped_column(Integer, primary_key=True)

            class UnsafeRunPayload(Base):
                __tablename__ = "unsafe_run_payloads"
                prompt = mapped_column(Text)
                raw_content = mapped_column(JSON)
                result = mapped_column("tool_result", Text)
            """
        ),
        encoding="utf-8",
    )

    violations = _scan_model_boundaries(models_root, project_root=tmp_path)

    assert {(violation.rule, violation.symbol) for violation in violations} == {
        ("RAW_CONTENT_FIELD", "UnsafeRunPayload.prompt"),
        ("RAW_CONTENT_FIELD", "UnsafeRunPayload.raw_content"),
        ("RAW_CONTENT_FIELD", "UnsafeRunPayload.tool_result"),
        ("RAW_EXECUTION_MODEL", "SpanRecord.__tablename__=agent_spans"),
    }


def test_boundary_scanner_detects_vendor_import_fixture(tmp_path: Path) -> None:
    services_root = tmp_path / "app" / "services"
    services_root.mkdir(parents=True)
    (services_root / "bad_provider_service.py").write_text(
        textwrap.dedent(
            """
            import importlib
            import langfuse
            from deepeval.metrics import AnswerRelevancyMetric

            atif_codec = importlib.import_module("atif.codec")
            """
        ),
        encoding="utf-8",
    )

    violations = _scan_vendor_imports((services_root,), project_root=tmp_path)

    assert {(violation.rule, violation.symbol) for violation in violations} == {
        ("VENDOR_SDK_IMPORT", "atif"),
        ("VENDOR_SDK_IMPORT", "deepeval"),
        ("VENDOR_SDK_IMPORT", "langfuse"),
    }


def test_mysql_models_do_not_add_raw_execution_storage() -> None:
    violations = _scan_model_boundaries(MODELS_ROOT, project_root=BACKEND_ROOT)
    observed = {violation.key for violation in violations}
    unexpected = [violation for violation in violations if violation.key not in LEGACY_MODEL_EXCEPTIONS]
    stale_exceptions = sorted(LEGACY_MODEL_EXCEPTIONS - observed)

    assert not unexpected and not stale_exceptions, (
        "DuckDock MySQL models must store governance indexes only.\n"
        f"Unexpected violations:\n{_violation_report(unexpected) or '<none>'}\n"
        f"Stale legacy exceptions: {stale_exceptions or '<none>'}"
    )


def test_core_services_and_ports_do_not_import_vendor_sdks() -> None:
    violations = _scan_vendor_imports(CORE_IMPORT_ROOTS, project_root=BACKEND_ROOT)
    observed = {violation.key for violation in violations}
    unexpected = [violation for violation in violations if violation.key not in LEGACY_VENDOR_IMPORT_EXCEPTIONS]
    stale_exceptions = sorted(LEGACY_VENDOR_IMPORT_EXCEPTIONS - observed)

    assert not unexpected and not stale_exceptions, (
        "Core services and Ports must depend on provider-neutral interfaces.\n"
        f"Unexpected violations:\n{_violation_report(unexpected) or '<none>'}\n"
        f"Stale legacy exceptions: {stale_exceptions or '<none>'}"
    )


def test_core_imports_with_vendor_sdks_blocked() -> None:
    package_names = ["app.models", "app.services"]
    if (APP_ROOT / "ports").exists():
        package_names.append("app.ports")

    script = textwrap.dedent(
        f"""
        import importlib
        import pkgutil
        import sys
        from importlib.abc import MetaPathFinder

        blocked_roots = {sorted(VENDOR_SDK_ROOTS)!r}
        package_names = {package_names!r}

        class VendorSdkBlocker(MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split('.', 1)[0] in blocked_roots:
                    raise ModuleNotFoundError(
                        f"blocked optional vendor SDK: {{fullname}}",
                        name=fullname,
                    )
                return None

        sys.meta_path.insert(0, VendorSdkBlocker())

        try:
            importlib.import_module('langfuse')
        except ModuleNotFoundError:
            pass
        else:
            raise AssertionError('vendor blocker did not hide an installed SDK')

        for package_name in package_names:
            package = importlib.import_module(package_name)
            package_path = getattr(package, '__path__', None)
            if package_path is None:
                continue
            module_names = sorted(
                module.name
                for module in pkgutil.walk_packages(package_path, package.__name__ + '.')
            )
            for module_name in module_names:
                importlib.import_module(module_name)
        """
    )

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(BACKEND_ROOT), existing_pythonpath) if part
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, (
        "DuckDock core failed to import with vendor SDKs blocked.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
