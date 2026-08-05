from __future__ import annotations

import json
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "ops/otel-collector/openclaw-shadow.yaml"
LANGFUSE_OVERLAY_PATH = (
    REPO_ROOT / "ops/otel-collector/langfuse-v4-overlay.yaml"
)
FIXTURE_PATH = (
    REPO_ROOT
    / "ops/otel-collector/fixtures/openclaw-shadow-trace.json"
)
GENERIC_CONFIG_PATH = (
    REPO_ROOT / "ops/otel-collector/generic-otlp-bridge.yaml"
)
GENERIC_FIXTURE_PATH = (
    REPO_ROOT / "ops/otel-collector/fixtures/generic-otlp-trace.json"
)


def _config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _langfuse_overlay() -> dict:
    return yaml.safe_load(
        LANGFUSE_OVERLAY_PATH.read_text(encoding="utf-8")
    )


def _generic_config() -> dict:
    return yaml.safe_load(
        GENERIC_CONFIG_PATH.read_text(encoding="utf-8")
    )


def test_openclaw_collector_requires_auth_for_every_receiver_protocol():
    config = _config()
    receiver = config["receivers"]["otlp/openclaw"]

    for protocol in ("grpc", "http"):
        assert receiver["protocols"][protocol]["auth"] == {
            "authenticator": "basicauth/runtime"
        }
        assert receiver["protocols"][protocol]["include_metadata"] is True

    assert "basicauth/runtime" in config["service"]["extensions"]
    assert (
        config["extensions"]["basicauth/runtime"]["htpasswd"]["inline"]
        == "${env:DUCKDOCK_OTEL_HTPASSWD}"
    )


def test_openclaw_collector_is_trace_only_and_orders_security_processors():
    config = _config()
    pipelines = config["service"]["pipelines"]

    assert set(pipelines) == {"traces/openclaw-shadow"}
    assert pipelines["traces/openclaw-shadow"]["processors"] == [
        "memory_limiter",
        "transform/strip_untrusted_governance",
        "gen_ai_normalizer/duckdock_v1",
        "filter/drop_content_surfaces",
        "transform/metadata_only",
        "resource/trusted_identity",
        "batch",
    ]
    assert pipelines["traces/openclaw-shadow"]["exporters"] == ["debug"]
    assert config["exporters"]["debug"]["verbosity"] == "detailed"


def test_openclaw_collector_fails_closed_and_injects_trusted_identity():
    config = _config()
    processors = config["processors"]

    assert (
        processors["transform/strip_untrusted_governance"]["error_mode"]
        == "propagate"
    )
    assert processors["transform/metadata_only"]["error_mode"] == "propagate"
    assert processors["filter/drop_content_surfaces"] == {
        "error_mode": "propagate",
        "traces": {
            "span": ["Len(span.links) > 0"],
            "spanevent": ["true"],
        },
    }

    trust_strip = json.dumps(
        processors["transform/strip_untrusted_governance"],
        sort_keys=True,
    )
    assert "delete_matching_keys" in trust_strip
    assert "^duckdock" in trust_strip
    assert "gen_ai" not in trust_strip
    assert "openinference" not in trust_strip

    normalizer = processors["gen_ai_normalizer/duckdock_v1"]
    assert normalizer["overwrite_schema_url"] is True
    assert normalizer["sources"] == [
        {
            "name": "duckdock.openclaw",
            "remove_originals": True,
            "overwrite": True,
            "mappings": {
                "openclaw.run.id": "duckdock.external_run_id",
                "openclaw.session.id": "duckdock.external_session_id",
                "openclaw.operation.name": "gen_ai.operation.name",
                "openclaw.model": "gen_ai.request.model",
            },
            "value_mappings": {
                "gen_ai.operation.name": {
                    "invoke_agent": "invoke_agent",
                    "execute_tool": "execute_tool",
                    "chat": "chat",
                }
            },
        },
        {
            "name": "duckdock.openinference.correlation",
            "remove_originals": False,
            "overwrite": False,
            "mappings": {
                "session.id": "duckdock.external_session_id",
            },
        },
        {
            "name": "openinference",
            "remove_originals": True,
            "overwrite": True,
        },
    ]
    normalizer_text = json.dumps(normalizer, sort_keys=True)
    assert "duckdock.external_run_id" in normalizer_text
    assert "duckdock.external_session_id" in normalizer_text
    assert "gen_ai.operation.name" in normalizer_text
    assert "gen_ai.request.model" in normalizer_text

    metadata_only = json.dumps(
        processors["transform/metadata_only"],
        sort_keys=True,
    )
    assert "keep_keys" in metadata_only
    assert "openclaw.agent.operation" in metadata_only
    statements_by_context = {
        group["context"]: group["statements"]
        for group in processors["transform/metadata_only"][
            "trace_statements"
        ]
    }
    assert 'set(resource.schema_url, "")' in statements_by_context[
        "resource"
    ]
    assert (
        'set(scope.name, "openclaw.instrumentation")'
        in statements_by_context["scope"]
    )
    assert (
        'set(scope.schema_url, '
        '"https://opentelemetry.io/schemas/1.40.0")'
        in statements_by_context["scope"]
    )
    assert 'set(span.status.message, "")' in statements_by_context["span"]
    assert 'set(span.trace_state, "")' in statements_by_context["span"]
    allowed_key_statements = [
        statement
        for group in processors["transform/metadata_only"][
            "trace_statements"
        ]
        for statement in group["statements"]
        if statement.startswith("keep_keys")
    ]
    allowed_keys = "\n".join(allowed_key_statements).lower()
    for forbidden in (
        "prompt",
        "completion",
        "message",
        "tool.arguments",
        "tool.result",
        "authorization",
        "cookie",
    ):
        assert forbidden not in allowed_keys

    actions = {
        item["key"]: item
        for item in processors["resource/trusted_identity"]["attributes"]
    }
    assert actions["duckdock.namespace.public_id"] == {
        "key": "duckdock.namespace.public_id",
        "value": "${env:DUCKDOCK_OTEL_NAMESPACE_PUBLIC_ID}",
        "action": "upsert",
    }
    assert actions["duckdock.runtime.public_id"] == {
        "key": "duckdock.runtime.public_id",
        "value": "${env:DUCKDOCK_OTEL_RUNTIME_PUBLIC_ID}",
        "action": "upsert",
    }
    assert actions["duckdock.credential.subject"]["from_context"] == (
        "auth.username"
    )
    assert actions["duckdock.trust.level"]["value"] == (
        "CHANNEL_AUTHENTICATED"
    )
    assert actions["duckdock.trust.source"]["value"] == "COLLECTOR"
    assert actions["duckdock.content_capture_mode"]["value"] == (
        "metadata_only"
    )
    assert actions["duckdock.normalizer.version"]["value"] == (
        "duckdock-genai-shadow-v1+otel-semconv-1.40.0"
    )


def test_openclaw_secret_canary_fixture_exercises_all_content_surfaces():
    fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")
    fixture = json.loads(fixture_text)

    assert fixture_text.count("SHADOW_SECRET_CANARY_DO_NOT_EXPORT") >= 6
    assert "forged-namespace" in fixture_text
    assert "forged-runtime" in fixture_text
    assert "forged-client-model" in fixture_text
    spans = fixture["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans[0]["events"]
    openinference_keys = {
        attribute["key"] for attribute in spans[1]["attributes"]
    }
    assert {
        "llm.model_name",
        "llm.provider",
        "llm.token_count.prompt",
        "llm.token_count.completion",
        "llm.input_messages.0.message.content",
        "llm.output_messages.0.message.content",
        "tool_call.function.arguments",
        "openinference.span.kind",
        "session.id",
    } <= openinference_keys
    otel_genai_keys = {
        attribute["key"] for attribute in spans[2]["attributes"]
    }
    assert {
        "gen_ai.request.model",
        "gen_ai.provider.name",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.tool.definitions",
        "gen_ai.operation.name",
        "gen_ai.conversation.id",
    } <= otel_genai_keys


def test_collector_compose_profile_is_optional_and_version_pinned():
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["otel-collector"]

    assert service["profiles"] == ["telemetry"]
    assert (
        service["image"]
        == "otel/opentelemetry-collector-contrib:0.157.0"
    )
    assert service["volumes"] == [
        "./ops/otel-collector/openclaw-shadow.yaml:/etc/otelcol/config.yaml:ro"
    ]
    assert all(
        str(port).startswith("127.0.0.1:")
        for port in service["ports"]
    )
    assert compose["services"]["otel-langfuse-mock"]["profiles"] == [
        "telemetry-test"
    ]


def test_langfuse_v4_overlay_replaces_debug_exporter_and_keeps_auth() -> None:
    overlay = _langfuse_overlay()
    exporter = overlay["exporters"]["otlp_http/langfuse"]
    storage = overlay["extensions"]["file_storage/langfuse"]

    assert exporter["endpoint"] == (
        "${env:DUCKDOCK_LANGFUSE_OTLP_ENDPOINT}"
    )
    assert exporter["auth"] == {
        "authenticator": "basicauth/langfuse"
    }
    assert exporter["headers"] == {
        "x-langfuse-ingestion-version": "4"
    }
    assert exporter["sending_queue"]["enabled"] is True
    assert exporter["sending_queue"]["storage"] == (
        "file_storage/langfuse"
    )
    assert exporter["sending_queue"]["block_on_overflow"] is True
    assert exporter["sending_queue"]["num_consumers"] == 1
    assert exporter["sending_queue"]["queue_size"] == (
        "${env:DUCKDOCK_OTEL_QUEUE_SIZE}"
    )
    assert exporter["retry_on_failure"]["enabled"] is True
    assert exporter["retry_on_failure"]["max_elapsed_time"] == "0s"
    assert storage == {
        "directory": "/var/lib/otelcol/langfuse",
        "timeout": "10s",
        "max_size": "${env:DUCKDOCK_OTEL_QUEUE_MAX_BYTES}",
        "fsync": True,
        "create_directory": True,
        "directory_permissions": "0700",
        "compaction": {
            "on_start": True,
            "directory": "/var/lib/otelcol/langfuse-compaction",
            "cleanup_on_start": True,
        },
    }
    assert overlay["service"]["extensions"] == [
        "health_check",
        "basicauth/runtime",
        "basicauth/langfuse",
        "file_storage/langfuse",
    ]
    assert overlay["service"]["telemetry"]["metrics"] == {
        "level": "basic",
        "readers": [
            {
                "pull": {
                    "exporter": {
                        "prometheus": {
                            "host": "0.0.0.0",
                            "port": 8888,
                            "without_type_suffix": True,
                            "without_units": True,
                        }
                    }
                }
            }
        ],
    }
    assert overlay["service"]["pipelines"]["traces/openclaw-shadow"][
        "exporters"
    ] == ["otlp_http/langfuse"]

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["otel-collector-langfuse"]
    assert service["profiles"] == ["telemetry-langfuse"]
    assert (
        service["image"]
        == "otel/opentelemetry-collector-contrib:0.157.0"
    )
    assert service["command"] == [
        "--config=file:/etc/otelcol/openclaw-shadow.yaml",
        "--config=file:/etc/otelcol/langfuse-v4-overlay.yaml",
    ]
    assert all(
        str(port).startswith("127.0.0.1:")
        for port in service["ports"]
    )
    assert (
        "otel_langfuse_queue_data:/var/lib/otelcol"
        in service["volumes"]
    )
    assert service["depends_on"]["otel-langfuse-queue-init"][
        "condition"
    ] == "service_completed_successfully"
    queue_init = compose["services"]["otel-langfuse-queue-init"]
    assert queue_init["profiles"] == ["telemetry-langfuse"]
    assert queue_init["user"] == "0:0"
    assert queue_init["volumes"] == [
        "otel_langfuse_queue_data:/var/lib/otelcol"
    ]
    assert "otel_langfuse_queue_data" in compose["volumes"]


def test_generic_otlp_bridge_has_two_durable_metadata_only_exports() -> None:
    config = _generic_config()
    receiver = config["receivers"]["otlp/generic"]
    for protocol in ("grpc", "http"):
        assert receiver["protocols"][protocol]["auth"] == {
            "authenticator": "basicauth/runtime"
        }

    pipeline = config["service"]["pipelines"]["traces/generic-otlp"]
    assert pipeline["processors"] == [
        "memory_limiter",
        "transform/strip_untrusted_governance",
        "filter/drop_content_surfaces",
        "transform/metadata_only",
        "resource/trusted_identity",
        "batch",
    ]
    assert pipeline["exporters"] == [
        "otlp_http/provider",
        "otlp_http/duckdock",
    ]
    for exporter_name in pipeline["exporters"]:
        exporter = config["exporters"][exporter_name]
        assert exporter["encoding"] == "json"
        assert exporter["compression"] == "none"
        assert exporter["retry_on_failure"]["max_elapsed_time"] == "0s"
        assert exporter["sending_queue"] == {
            "enabled": True,
            "storage": "file_storage/generic",
            "num_consumers": 1,
            "block_on_overflow": True,
            "sizer": "requests",
            "queue_size": "${env:DUCKDOCK_OTEL_QUEUE_SIZE}",
        }

    duckdock_exporter = config["exporters"]["otlp_http/duckdock"]
    assert duckdock_exporter["traces_endpoint"].endswith(
        "/api/v2/reporter/telemetry-sinks/"
        "${env:DUCKDOCK_TELEMETRY_SINK_PUBLIC_ID}/v1/traces"
    )
    assert duckdock_exporter["headers"]["Authorization"] == (
        "Bearer ${env:DUCKDOCK_GENERIC_REPORTER_TOKEN}"
    )

    processors = json.dumps(config["processors"], sort_keys=True)
    assert "delete_matching_keys" in processors
    assert "duckdock.namespace.public_id" in processors
    assert "duckdock.runtime.public_id" in processors
    assert "keep_keys" in processors
    allowed_statements = "\n".join(
        statement
        for group in config["processors"]["transform/metadata_only"][
            "trace_statements"
        ]
        for statement in group["statements"]
        if statement.startswith("keep_keys")
    ).lower()
    for forbidden in (
        "prompt",
        "messages",
        "tool.arguments",
        "tool.result",
        "authorization",
        "cookie",
    ):
        assert forbidden not in allowed_statements


def test_generic_otlp_fixture_and_compose_profile_are_bounded() -> None:
    fixture_text = GENERIC_FIXTURE_PATH.read_text(encoding="utf-8")
    assert fixture_text.count("GENERIC_SECRET_CANARY_DO_NOT_EXPORT") >= 6
    assert "forged-namespace" in fixture_text
    assert "forged-runtime" in fixture_text
    assert "generic-run-smoke-001" in fixture_text

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["otel-collector-generic"]
    assert service["profiles"] == ["telemetry-generic"]
    assert (
        service["image"]
        == "otel/opentelemetry-collector-contrib:0.157.0"
    )
    assert all(
        str(port).startswith("127.0.0.1:")
        for port in service["ports"]
    )
    assert (
        "otel_generic_queue_data:/var/lib/otelcol"
        in service["volumes"]
    )
    assert service["depends_on"]["otel-generic-queue-init"][
        "condition"
    ] == "service_completed_successfully"
    assert compose["services"]["otel-generic-queue-init"]["user"] == "0:0"
    assert "otel_generic_queue_data" in compose["volumes"]
