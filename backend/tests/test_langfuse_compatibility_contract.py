from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.adapters.evaluation.langfuse import (
    LangfuseExperimentRunnerError,
    build_langfuse_experiment_runner,
)
from app.core.config import Settings
from app.services.langfuse_compatibility import (
    LANGFUSE_COMPATIBILITY_PROFILE,
    LANGFUSE_OTLP_INGESTION_VERSION,
    LANGFUSE_SDK_BASELINE_VERSION,
    require_langfuse_sdk_compatibility,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_langfuse_sdk_requires_the_accepted_exact_baseline(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.langfuse_compatibility.metadata.version",
        lambda _package: LANGFUSE_SDK_BASELINE_VERSION,
    )
    assert (
        require_langfuse_sdk_compatibility()
        == LANGFUSE_SDK_BASELINE_VERSION
    )

    monkeypatch.setattr(
        "app.services.langfuse_compatibility.metadata.version",
        lambda _package: "4.99.0",
    )
    with pytest.raises(RuntimeError, match="has not passed"):
        require_langfuse_sdk_compatibility()

    monkeypatch.setattr(
        "app.services.langfuse_compatibility.metadata.version",
        lambda _package: "5.0.0",
    )
    with pytest.raises(RuntimeError, match="requires SDK major 4"):
        require_langfuse_sdk_compatibility(expected_version="5.0.0")


def test_settings_and_runner_fail_closed_for_unknown_profile() -> None:
    settings = Settings(
        _env_file=None,
        LANGFUSE_COMPATIBILITY_PROFILE=LANGFUSE_COMPATIBILITY_PROFILE,
    )
    assert (
        settings.LANGFUSE_COMPATIBILITY_PROFILE
        == LANGFUSE_COMPATIBILITY_PROFILE
    )
    with pytest.raises(ValidationError, match="register a new adapter"):
        Settings(
            _env_file=None,
            LANGFUSE_COMPATIBILITY_PROFILE="langfuse-v5",
        )

    with pytest.raises(
        LangfuseExperimentRunnerError,
        match="Unsupported Langfuse compatibility profile",
    ):
        build_langfuse_experiment_runner(
            compatibility_profile="langfuse-v5",
            client=object(),
            target_adapter=object(),
        )


def test_langfuse_runtime_dependencies_are_exactly_pinned() -> None:
    requirements = (
        REPO_ROOT / "backend" / "requirements.txt"
    ).read_text(encoding="utf-8")
    assert f"langfuse=={LANGFUSE_SDK_BASELINE_VERSION}" in requirements
    assert "langfuse>=" not in requirements

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    assert compose["services"]["langfuse-web"]["image"].endswith(
        "langfuse:${LANGFUSE_IMAGE_TAG:-4.1.0}"
    )
    assert compose["services"]["langfuse-worker"]["image"].endswith(
        "langfuse-worker:${LANGFUSE_IMAGE_TAG:-4.1.0}"
    )
    assert compose["services"]["clickhouse"]["image"].endswith(
        ":${LANGFUSE_CLICKHOUSE_TAG:-25.12.11.4}"
    )
    assert compose["services"]["postgres"]["image"] == (
        "pgvector/pgvector:0.8.6-pg16"
    )
    assert compose["services"]["redis"]["image"] == "redis:7.4.9-alpine"
    assert compose["services"]["minio"]["image"] == (
        "minio/minio:RELEASE.2025-09-07T16-13-09Z"
    )
    assert compose["services"]["langfuse-minio-init"]["image"] == (
        "minio/mc:RELEASE.2025-08-13T08-35-41Z"
    )

    production_compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.prod.yml").read_text(
            encoding="utf-8"
        )
    )
    assert production_compose["services"]["redis"]["image"] == (
        "redis:7.4.9-alpine"
    )
    assert production_compose["services"]["minio"]["image"] == (
        "minio/minio:RELEASE.2025-09-07T16-13-09Z"
    )
    assert production_compose["services"]["minio-init"]["image"] == (
        "minio/mc:RELEASE.2025-08-13T08-35-41Z"
    )


def test_otlp_overlay_matches_the_registered_v4_profile() -> None:
    overlay = yaml.safe_load(
        (
            REPO_ROOT
            / "ops"
            / "otel-collector"
            / "langfuse-v4-overlay.yaml"
        ).read_text(encoding="utf-8")
    )
    assert overlay["exporters"]["otlp_http/langfuse"]["headers"] == {
        "x-langfuse-ingestion-version": (
            LANGFUSE_OTLP_INGESTION_VERSION
        )
    }


def test_upgrade_gate_covers_trace_to_dataset_contract() -> None:
    source = (
        REPO_ROOT / "backend" / "scripts" / "verify_langfuse_compatibility.py"
    ).read_text(encoding="utf-8")
    assert 'fields="core,basic,time,io"' in source
    assert 'fields="core,basic,time"' in source
    assert "is_root_observation=True" in source
    assert "content-free curation candidate query" in source
    assert "source_trace_id=source_trace_id" in source
    assert "source_observation_id" in source
    assert "Trace2Dataset source linkage was not preserved" in source
    assert "LangfuseAnnotationQueueAdapter" in source
    assert "Annotation Queue dispatch is not idempotent" in source
    assert "Annotation Queue completion did not reconcile" in source
    assert "FIXTURE_PROMOTION_QUEUE_NAME" in source
    assert "CreateScoreSource.ANNOTATION" in source
    assert "PromotionQualityRule" in source
    assert "adapter.evaluate_promotion_evidence" in source
    assert "Scores API v3 Promotion evidence" in source

    gate = (REPO_ROOT / "scripts" / "verify-langfuse-upgrade.sh").read_text(
        encoding="utf-8"
    )
    assert "tests/test_evaluation_promotion.py" in gate
    assert "tests/test_evaluation_case_routing.py" in gate
    assert "tests/test_failure_taxonomy_experience.py" in gate
