#!/usr/bin/env python3
"""Verify DuckDock's production Compose and HA reference deployment baseline."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Check:
    key: str
    passed: bool
    observed: str
    expected: str


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed: {completed.stderr.strip()}")
    return completed.stdout


def verify(env_file: Path) -> dict[str, Any]:
    checks: list[Check] = []

    def add(key: str, passed: bool, observed: Any, expected: str) -> None:
        checks.append(Check(key, bool(passed), str(observed), expected))

    environment = os.environ.copy()
    environment["DUCKDOCK_PROD_ENV_FILE"] = str(env_file)
    compose_json = _run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            "docker-compose.prod.yml",
            "-f",
            "docker-compose.prod-tls.yml",
            "config",
            "--format",
            "json",
        ],
        env=environment,
    )
    compose = json.loads(compose_json)
    services = compose["services"]
    networks = compose["networks"]

    public_ports: list[tuple[str, str, int]] = []
    loopback_ports: list[tuple[str, str, int]] = []
    for service_name, service in services.items():
        for port in service.get("ports") or []:
            binding = (service_name, str(port.get("host_ip", "")), int(port["published"]))
            if port.get("host_ip") in {"127.0.0.1", "::1"}:
                loopback_ports.append(binding)
            else:
                public_ports.append(binding)
    add(
        "public_ports",
        public_ports == [("tls-gateway", "0.0.0.0", 443)],
        public_ports,
        "only tls-gateway 0.0.0.0:443",
    )
    add(
        "data_store_loopback",
        all(
            not (name in {"mysql", "redis"} and host not in {"127.0.0.1", "::1"})
            for name, host, _ in public_ports + loopback_ports
        ),
        loopback_ports,
        "MySQL/Redis unbound; MinIO/UI/monitoring loopback only",
    )
    internal_networks = {
        name for name, config in networks.items() if isinstance(config, dict) and config.get("internal") is True
    }
    add(
        "network_segmentation",
        {"app_internal", "data_internal", "observability_internal"}.issubset(internal_networks),
        sorted(internal_networks),
        "app_internal, data_internal and observability_internal are internal",
    )
    data_members = {
        name
        for name, service in services.items()
        if "data_internal" in (service.get("networks") or {})
    }
    add(
        "data_network_membership",
        {"mysql", "redis", "minio", "backend", "worker", "beat", "migrate"}.issubset(data_members)
        and "frontend" not in data_members
        and "tls-gateway" not in data_members,
        sorted(data_members),
        "state services and trusted app jobs only; no frontend/gateway",
    )

    compose_text = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    image_lines = [line.strip() for line in compose_text.splitlines() if line.strip().startswith("image:")]
    add(
        "pinned_third_party_images",
        bool(image_lines) and all("@sha256:" in line for line in image_lines),
        image_lines,
        "every third-party production image is digest pinned",
    )
    alertmanager_dockerfile = (REPO_ROOT / "ops/alertmanager/Dockerfile").read_text(encoding="utf-8")
    add(
        "patched_alertmanager_supply_chain",
        alertmanager_dockerfile.count("2c8da51e03f3dbbed24f9711ca2d76aab4eef9c5") >= 3
        and "golang.org/x/crypto@v0.52.0" in alertmanager_dockerfile
        and "google.golang.org/grpc@v1.82.1" in alertmanager_dockerfile
        and "FROM scratch" in alertmanager_dockerfile
        and "USER 65534:65534" in alertmanager_dockerfile,
        "upstream commit plus post-release dependency fixes; scratch/non-root runtime",
        "exact source commit; fixed crypto/gRPC; minimal non-root runtime",
    )
    prometheus = (REPO_ROOT / "ops/prometheus/prometheus.yml").read_text(encoding="utf-8")
    alerts = (REPO_ROOT / "ops/prometheus/duckdock-alerts.yml").read_text(encoding="utf-8")
    add(
        "alert_delivery_wiring",
        "alertmanager:9093" in prometheus
        and "DuckDockAlertmanagerUnavailable" in alerts
        and "DuckDockAlertDeliveryFailing" in alerts,
        "Prometheus -> Alertmanager plus delivery failure alerts",
        "notification target and meta-alerts configured",
    )
    ci_workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    local_preflight_path = REPO_ROOT / "scripts/run_ga_local_preflight.py"
    local_preflight_text = (
        local_preflight_path.read_text(encoding="utf-8") if local_preflight_path.is_file() else ""
    )
    security_preaudit_path = REPO_ROOT / "scripts/run_security_preaudit.py"
    security_preaudit_text = (
        security_preaudit_path.read_text(encoding="utf-8")
        if security_preaudit_path.is_file()
        else ""
    )
    gitleaks_runner_path = REPO_ROOT / "scripts/run-gitleaks.sh"
    gitleaks_runner_text = (
        gitleaks_runner_path.read_text(encoding="utf-8")
        if gitleaks_runner_path.is_file()
        else ""
    )
    gitleaks_baseline_path = REPO_ROOT / ".gitleaksignore"
    gitleaks_fingerprints = (
        [
            line
            for line in gitleaks_baseline_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        if gitleaks_baseline_path.is_file()
        else []
    )
    ha_target_bundle_path = REPO_ROOT / "scripts/prepare-kubernetes-ha-target.py"
    ha_target_bundle_text = (
        ha_target_bundle_path.read_text(encoding="utf-8")
        if ha_target_bundle_path.is_file()
        else ""
    )
    ha_target_bundle_tests = REPO_ROOT / "backend/tests/test_kubernetes_ha_target_bundle.py"
    provenance_components = (
        REPO_ROOT / "backend/scripts/ga_release_provenance.py",
        REPO_ROOT / "backend/scripts/collect_ga_release_provenance.py",
        REPO_ROOT / "ops/ga/release-provenance-trust-policy.example.json",
        REPO_ROOT / "ops/ga/build-provenance-report.example.json",
        REPO_ROOT / "ops/ga/release-scout-sarif.example.json",
    )
    add(
        "ga_release_supply_chain",
        "tags:\n      - v2.0.0" in ci_workflow
        and "needs: [backend, frontend, e2e, compose]" in ci_workflow
        and "Verify final signed tag" in ci_workflow
        and "Promote scanned image indexes to previously unused final tags" in ci_workflow
        and "final tag already exists; refusing overwrite" in ci_workflow
        and "could not prove final tag is absent" in ci_workflow
        and ci_workflow.count("provenance: mode=max,version=v1") == 4
        and ci_workflow.count("sbom: true") == 4
        and ci_workflow.count("sarif-file:") == 4
        and "Retain final vulnerability scan reports" in ci_workflow
        and "if-no-files-found: error" in ci_workflow
        and all(path.is_file() for path in provenance_components),
        "signed v2.0.0 after full CI; BuildKit attestations; retained SARIF; strict signed-report collector",
        "final tag reruns all CI gates and emits independently re-verifiable release evidence",
    )
    add(
        "ga_local_preflight_handoff",
        "duckdock-ga-local-preflight-v1" in local_preflight_text
        and "working tree must be clean before a GA local preflight" in local_preflight_text
        and "PENDING_EXTERNAL" in local_preflight_text
        and "non-authorizing local preflight" in local_preflight_text
        and "backend_tests_with_coverage" in local_preflight_text
        and "live_langfuse_compatibility" in local_preflight_text
        and "run_ga_local_preflight.py --help" in ci_workflow,
        "clean source; repository/integrated gates; content-addressed receipt; six external requirements stay pending",
        "one local command gives release operators a reproducible handoff without claiming GA authorization",
    )
    add(
        "ga_internal_security_preaudit",
        "duckdock-security-preaudit-v1" in security_preaudit_text
        and "working tree must be clean before the security pre-audit"
        in security_preaudit_text
        and "not an independent security assessment" in security_preaudit_text
        and "PENDING_EXTERNAL" in security_preaudit_text
        and "tracked_secret_heuristic" in security_preaudit_text
        and 'key="gitleaks_full_history"' in security_preaudit_text
        and "critical,high" in security_preaudit_text
        and "possibly stale tag" in security_preaudit_text
        and "v8.30.1@sha256:c00b6bd0" in gitleaks_runner_text
        and "--redact=100" in gitleaks_runner_text
        and "--network none" in gitleaks_runner_text
        and "--read-only" in gitleaks_runner_text
        and len(gitleaks_fingerprints) == 33
        and len(set(gitleaks_fingerprints)) == 33
        and "Scan full Git history for secrets" in ci_workflow
        and "fetch-depth: 0" in ci_workflow
        and "Retain redacted Gitleaks report" in ci_workflow
        and "run_security_preaudit.py --help" in ci_workflow
        and "High-confidence security static gate" in ci_workflow,
        "clean source; dependency/SAST/negative/full-history-secret/image gates; content-addressed non-independent receipt",
        "internal pre-audit reduces assessor rework without replacing independent testing or GA authorization",
    )
    add(
        "ha_target_bundle_preparation",
        ha_target_bundle_path.is_file()
        and os.access(ha_target_bundle_path, os.X_OK)
        and ha_target_bundle_tests.is_file()
        and "duckdock-kubernetes-ha-target-bundle-v1" in ha_target_bundle_text
        and "working tree must be clean before preparing a target HA bundle"
        in ha_target_bundle_text
        and "PREPARED_NOT_AUTHORIZED" in ha_target_bundle_text
        and all(name in ha_target_bundle_text for name in ("bootstrap.yaml", "migration.yaml", "applications.yaml"))
        and "must be a lowercase registry image pinned by @sha256"
        in ha_target_bundle_text
        and "bundle directory has missing or unexpected entries"
        in ha_target_bundle_text
        and "bundle external requirements changed" in ha_target_bundle_text
        and "server-side dry-run bootstrap.yaml, migration.yaml and applications.yaml"
        in ha_target_bundle_text
        and "Validate Kubernetes HA target bundle CLI" in ci_workflow,
        "clean source and immutable images; exact three-phase target manifests; target values and pending external duties are tamper checked",
        "one generated bundle separates bootstrap, commit-bound migration and application rollout without claiming target authorization",
    )
    preapproval_assembler = REPO_ROOT / "backend/scripts/assemble_ga_preapproval_authorization.py"
    trust_topology_verifier = REPO_ROOT / "backend/scripts/verify_ga_trust_topology.py"
    trust_topology_manifest = REPO_ROOT / "ops/ga/trust-topology-manifest.example.json"
    execution_campaign_preparer = REPO_ROOT / "backend/scripts/prepare_ga_execution_campaign.py"
    execution_campaign_inspector = REPO_ROOT / "backend/scripts/inspect_ga_execution_campaign.py"
    execution_campaign_closer = REPO_ROOT / "backend/scripts/close_ga_execution_campaign.py"
    execution_authorization_core = REPO_ROOT / "backend/scripts/ga_execution_authorization.py"
    execution_authorization_preparer = (
        REPO_ROOT / "backend/scripts/prepare_ga_execution_authorization.py"
    )
    execution_authorization_signer = (
        REPO_ROOT / "backend/scripts/sign_ga_execution_authorization.py"
    )
    execution_authorization_verifier = (
        REPO_ROOT / "backend/scripts/verify_ga_execution_authorization.py"
    )
    target_cluster_identity_core = REPO_ROOT / "backend/scripts/ga_target_cluster_identity.py"
    target_cluster_identity_collector = (
        REPO_ROOT / "backend/scripts/collect_ga_target_cluster_identity.py"
    )
    target_cluster_access_core = REPO_ROOT / "backend/scripts/ga_target_cluster_access.py"
    target_cluster_access_collector = (
        REPO_ROOT / "backend/scripts/collect_ga_target_cluster_access.py"
    )
    execution_phase_start_core = REPO_ROOT / "backend/scripts/ga_execution_phase_start.py"
    execution_phase_starter = REPO_ROOT / "backend/scripts/start_ga_execution_phase.py"
    execution_runtime_entrypoints = tuple(
        (REPO_ROOT / relative, phase_id, effect)
        for relative, phase_id, effect in (
            ("backend/scripts/probe_ga_target_tls.py", "tls", "result = probe(args)"),
            ("backend/scripts/collect_ga_target_network.py", "network", "report = collect(args)"),
            ("backend/scripts/collect_ga_target_secrets.py", "secrets", "report = collect(args)"),
            ("backend/scripts/g2_target_capacity_gate.py", "capacity", "await run_gate(args)"),
            ("backend/scripts/collect_ga_target_alerting.py", "alerting", "report = collect(args)"),
            ("backend/scripts/collect_ga_target_recovery.py", "recovery", "report = collect(args)"),
            ("backend/scripts/collect_ga_state_services_ha.py", "state_services", "report = collect(args)"),
            ("backend/scripts/collect_ga_target_ha.py", "high_availability", "report = execute(args)"),
        )
    )
    execution_campaign_request = REPO_ROOT / "ops/ga/execution-campaign-request.example.json"
    approval_freezer = REPO_ROOT / "backend/scripts/freeze_ga_approval_campaign.py"
    approval_signer = REPO_ROOT / "backend/scripts/sign_ga_approval.py"
    approval_finalizer = REPO_ROOT / "backend/scripts/finalize_ga_authorization.py"
    authorization_verifier = REPO_ROOT / "backend/scripts/verify_ga_production_authorization.py"
    authorized_archiver = REPO_ROOT / "backend/scripts/archive_ga_authorized_bundle.py"
    authorized_archive_verifier = REPO_ROOT / "backend/scripts/verify_ga_authorized_archive.py"
    publication_authorizer = REPO_ROOT / "backend/scripts/authorize_ga_publication.py"
    publication_workflow = REPO_ROOT / ".github/workflows/publish-ga.yml"
    ga_path_resolution = REPO_ROOT / "backend/scripts/ga_path_resolution.py"
    assembler_text = (
        preapproval_assembler.read_text(encoding="utf-8")
        if preapproval_assembler.is_file()
        else ""
    )
    trust_topology_text = (
        trust_topology_verifier.read_text(encoding="utf-8")
        if trust_topology_verifier.is_file()
        else ""
    )
    execution_campaign_text = (
        execution_campaign_preparer.read_text(encoding="utf-8")
        if execution_campaign_preparer.is_file()
        else ""
    )
    execution_progress_text = (
        execution_campaign_inspector.read_text(encoding="utf-8")
        if execution_campaign_inspector.is_file()
        else ""
    )
    execution_closure_text = (
        execution_campaign_closer.read_text(encoding="utf-8")
        if execution_campaign_closer.is_file()
        else ""
    )
    execution_authorization_text = (
        execution_authorization_core.read_text(encoding="utf-8")
        if execution_authorization_core.is_file()
        else ""
    )
    execution_authorization_preparer_text = (
        execution_authorization_preparer.read_text(encoding="utf-8")
        if execution_authorization_preparer.is_file()
        else ""
    )
    execution_authorization_signer_text = (
        execution_authorization_signer.read_text(encoding="utf-8")
        if execution_authorization_signer.is_file()
        else ""
    )
    target_cluster_identity_text = (
        target_cluster_identity_core.read_text(encoding="utf-8")
        if target_cluster_identity_core.is_file()
        else ""
    )
    target_cluster_identity_collector_text = (
        target_cluster_identity_collector.read_text(encoding="utf-8")
        if target_cluster_identity_collector.is_file()
        else ""
    )
    target_cluster_access_text = (
        target_cluster_access_core.read_text(encoding="utf-8")
        if target_cluster_access_core.is_file()
        else ""
    )
    target_cluster_access_collector_text = (
        target_cluster_access_collector.read_text(encoding="utf-8")
        if target_cluster_access_collector.is_file()
        else ""
    )
    execution_phase_start_text = (
        execution_phase_start_core.read_text(encoding="utf-8")
        if execution_phase_start_core.is_file()
        else ""
    )
    execution_phase_starter_text = (
        execution_phase_starter.read_text(encoding="utf-8")
        if execution_phase_starter.is_file()
        else ""
    )
    execution_runtime_entrypoint_texts = tuple(
        (
            path.read_text(encoding="utf-8") if path.is_file() else "",
            phase_id,
            effect,
        )
        for path, phase_id, effect in execution_runtime_entrypoints
    )
    freezer_text = (
        approval_freezer.read_text(encoding="utf-8") if approval_freezer.is_file() else ""
    )
    signer_text = approval_signer.read_text(encoding="utf-8") if approval_signer.is_file() else ""
    finalizer_text = (
        approval_finalizer.read_text(encoding="utf-8")
        if approval_finalizer.is_file()
        else ""
    )
    authorization_verifier_text = (
        authorization_verifier.read_text(encoding="utf-8")
        if authorization_verifier.is_file()
        else ""
    )
    archiver_text = (
        authorized_archiver.read_text(encoding="utf-8")
        if authorized_archiver.is_file()
        else ""
    )
    archive_verifier_text = (
        authorized_archive_verifier.read_text(encoding="utf-8")
        if authorized_archive_verifier.is_file()
        else ""
    )
    publication_authorizer_text = (
        publication_authorizer.read_text(encoding="utf-8")
        if publication_authorizer.is_file()
        else ""
    )
    publication_workflow_text = (
        publication_workflow.read_text(encoding="utf-8")
        if publication_workflow.is_file()
        else ""
    )
    path_resolution_text = (
        ga_path_resolution.read_text(encoding="utf-8")
        if ga_path_resolution.is_file()
        else ""
    )
    add(
        "ga_trust_topology",
        trust_topology_manifest.is_file()
        and "duckdock-ga-trust-topology-manifest-v1" in trust_topology_text
        and "global GA trust separation failed" in trust_topology_text
        and '"organizational_trust_separation"' in authorization_verifier_text,
        "immutable nine-policy preflight plus independently recomputed final authorization gate",
        "every GA identity and public key is globally exclusive across organizational duties",
    )
    add(
        "ga_execution_campaign",
        execution_campaign_request.is_file()
        and "PLANNED_EXTERNAL_EXECUTION" in execution_campaign_text
        and "PENDING_EXTERNAL_EVIDENCE" in execution_campaign_text
        and "does_not_authorize_GA_or_target_mutation" in execution_campaign_text
        and "destructive recovery target must differ" in execution_campaign_text
        and "execution phases must produce every planned artifact exactly once"
        in execution_campaign_text
        and '"release_source_archive"' in execution_campaign_text
        and "persisted execution campaign did not independently re-verify" in execution_campaign_text,
        "content-addressed release/target/topology plan with fresh evidence root and pending external phases",
        "operators share one dependency graph and generated preapproval request without claiming evidence PASS",
    )
    add(
        "ga_execution_dual_authorization",
        execution_authorization_verifier.is_file()
        and "authorizes_only_named_campaign_phases_not_GA_or_unlisted_mutation"
        in execution_authorization_text
        and "execution authorization requires exact Security and Operations statements"
        in execution_authorization_text
        and "must be signed after preparation and before window start"
        in execution_authorization_text
        and "--prepared-at" not in execution_authorization_preparer_text
        and "--signed-at" not in execution_authorization_signer_text
        and '"execution_authorization"' in execution_campaign_text
        and "verify_execution_authorization" in execution_progress_text
        and "verify_execution_authorization" in execution_closure_text,
        "campaign-bound Security and Operations signatures are reverified by progress, closure and archive paths",
        "two distinct pre-window role signatures authorize only the named acknowledged execution phases",
    )
    add(
        "ga_execution_campaign_progress",
        "does_not_authorize_GA_or_target_mutation_or_evidence_PASS"
        in execution_progress_text
        and "unplanned_reference" in execution_progress_text
        and "symbolic_link_forbidden" in execution_progress_text
        and "READY_FOR_CLOSURE_ATTEMPT" in execution_progress_text
        and "EXPIRED_INCOMPLETE" in execution_progress_text
        and "verify_persisted_closure" in execution_progress_text,
        "immutable non-authorizing checkpoints detect partial/missing/invalid evidence and exact next phase",
        "external execution mistakes are visible before the final 91-artifact closure attempt",
    )
    add(
        "ga_target_cluster_identity_binding",
        target_cluster_identity_collector.is_file()
        and "duckdock-ga-target-cluster-identity-v1" in target_cluster_identity_text
        and '["auth", "whoami"]' in target_cluster_identity_text
        and '["get", "namespace", "kube-system"]' in target_cluster_identity_text
        and "live Kubernetes cluster UID or principal differs" in target_cluster_identity_text
        and "live Kubernetes cluster UID or principal changed" in target_cluster_identity_text
        and "kubernetes_cluster_uid" in execution_campaign_text
        and "kubernetes_principal" in execution_campaign_text
        and "verify_target_cluster_identity" in execution_progress_text
        and "verify_target_cluster_identity" in execution_closure_text
        and "--operations-identity" in target_cluster_identity_collector_text
        and "--observed-at" not in target_cluster_identity_collector_text,
        "signed Operations observation binds kube-system UID and authenticated principal",
        "a context-name substitution or credential switch is detectable and signed",
    )
    add(
        "ga_target_cluster_access_binding",
        target_cluster_access_collector.is_file()
        and "duckdock-ga-target-cluster-access-v1" in target_cluster_access_text
        and '"can-i"' in target_cluster_access_text
        and "target Kubernetes permission differs from the least-privilege profile"
        in target_cluster_access_text
        and "runtime Kubernetes operational scope differs from the campaign"
        in target_cluster_access_text
        and "change_request_id" in execution_campaign_text
        and "kubernetes_scope" in execution_campaign_text
        and "verify_target_cluster_access" in execution_progress_text
        and "verify_target_cluster_access" in execution_closure_text
        and "--operations-identity" in target_cluster_access_collector_text
        and "--observed-at" not in target_cluster_access_collector_text
        and all(
            "verify_live_target_cluster_access(" in text
            and text.index("verify_runtime_entry(")
            < text.index("verify_live_target_cluster_access(")
            < text.index(effect)
            for text, phase_id, effect in execution_runtime_entrypoint_texts
            if phase_id in {"network", "secrets", "high_availability"}
        ),
        "signed Operations allow/deny RBAC receipt binds the exact change and Kubernetes scope; risky CLIs recheck it live",
        "identity, permission or Secret/CNI/probe/zone scope drift fails before target probing or mutation",
    )
    add(
        "ga_execution_phase_interlocks",
        execution_phase_starter.is_file()
        and "duckdock-ga-execution-phase-start-v1" in execution_phase_start_text
        and "authorizes_only_one_named_campaign_phase_start_not_GA_or_unlisted_mutation"
        in execution_phase_start_text
        and "phase start identity must be the exact Operations authorizer"
        in execution_phase_start_text
        and "completed dependency artifact" in execution_phase_start_text
        and "started before its signed phase interlock" in execution_closure_text
        and "signed_phase_start_interlock_invalid" in execution_progress_text
        and "--action-description" in execution_phase_starter_text
        and "--action-sha256" not in execution_phase_starter_text
        and "--started-at" not in execution_phase_starter_text,
        "eight immutable Operations-signed starts bind the campaign, phase, dependencies and reviewed action",
        "risky evidence is rejected when execution predates its signed interlock or reuses/tampers with a permit",
    )
    add(
        "ga_execution_runtime_entry_interlocks",
        "duckdock-ga-execution-runtime-entry-v1" in execution_phase_start_text
        and "RUNTIME_ENTRY_AUTHORIZED" in execution_phase_start_text
        and "runtime entry does not match the signed phase action" in execution_phase_start_text
        and len(execution_runtime_entrypoint_texts) == 8
        and all(
            "verify_runtime_entry(" in text
            and f'phase_id="{phase_id}"' in text
            and "--execution-campaign" in text
            and "--phase-action-id" in text
            and effect in text
            and text.index("verify_runtime_entry(") < text.index(effect)
            for text, phase_id, effect in execution_runtime_entrypoint_texts
        ),
        "all eight official target CLIs reverify the signed permit before probing or mutating the target",
        "campaign, phase action, release images, target and relevant execution context must match at process entry",
    )
    add(
        "ga_preapproval_assembly",
        "exactly the nine required controls" in assembler_text
        and 'result.get("campaign_stage") == "APPROVAL_COLLECTION"' in assembler_text
        and "persisted preapproval authorization did not re-verify" in assembler_text
        and "preapproval assembly input changed before receipt emission" in assembler_text,
        "nine release-bound evidence reports are projected and re-evaluated into one immutable approval-empty base",
        "no manual control projection; emit only at APPROVAL_COLLECTION with foundation/evidence clean",
    )
    add(
        "ga_execution_campaign_closure",
        "duckdock-ga-execution-campaign-closure-v4" in execution_closure_text
        and "campaign evidence closure is not exact" in execution_closure_text
        and "execution campaign can close only inside its bound execution window"
        in execution_closure_text
        and "PREAPPROVAL_ASSEMBLED" in execution_closure_text
        and "campaign input changed before closure receipt emission"
        in execution_closure_text,
        "exact 91-artifact/reference closure including dual authorization, signed cluster identity/access, eight phase starts and security engagement",
        "planned paths equal actual evidence before immutable non-authorizing closure",
    )
    add(
        "ga_approval_campaign",
        'evaluation.get("campaign_stage") == "APPROVAL_COLLECTION"' in freezer_text
        and "campaign authorization approvals must be empty" in freezer_text
        and "verify_persisted_closure" in freezer_text
        and '"execution_closure"' in freezer_text
        and "require_execution_closure=not args.allow_legacy_unbound" in signer_text
        and 'preflight.get("campaign_stage") == "APPROVAL_COLLECTION"' in signer_text
        and 'preflight.get("evidence_ready_for_approval") is True' in signer_text
        and "approved-at cannot exceed the approval campaign expiry" in signer_text
        and "new approval did not pass authoritative verification" in signer_text
        and 'result.get("status") == "GA_AUTHORIZED"' in finalizer_text
        and "require_execution_closure=not args.allow_legacy_unbound" in finalizer_text
        and "do not all belong to the frozen approval campaign" in finalizer_text
        and "persisted final authorization did not re-verify" in finalizer_text
        and "campaign freeze bound files cannot be resolved" in authorization_verifier_text
        and 'expected_base.pop("approval_campaign", None)' in authorization_verifier_text,
        "closure-bound v2 freeze; each signer re-evaluates; mixed campaigns rejected; persisted GA_AUTHORIZED recheck",
        "one bounded campaign binds execution closure/base/policy/digest; four approvals and final output pass the same gate",
    )
    add(
        "ga_authorized_archive",
        'result.get("status") == "GA_AUTHORIZED"' in archiver_text
        and 'result.get("next_action") == "archive_authorized_bundle"' in archiver_text
        and "referenced file is outside every allowed root" in archiver_text
        and "symbolic-link evidence is forbidden" in archiver_text
        and "private-key material is forbidden" in archiver_text
        and "authorization evaluation changed during archive creation" in archiver_text
        and "require_formal_campaign_freeze" in archiver_text
        and "GzipFile" in archiver_text
        and "mtime=0" in archiver_text,
        "explicit reference closure; allowed-root isolation; double GA evaluation; deterministic immutable archive",
        "authorized bundle is archived without directory scans or adjacent private keys",
    )
    add(
        "ga_authorized_archive_verification",
        "GA_AUTHORIZED_ARCHIVE_VERIFIED" in archive_verifier_text
        and "manifest reference index does not exactly match" in archive_verifier_text
        and "archived authorization did not independently re-evaluate"
        in archive_verifier_text
        and "ga_file_resolution_overrides(overrides, strict=True)" in archive_verifier_text
        and "require_formal_campaign_freeze" in archive_verifier_text
        and "return (True, None) if strict" in path_resolution_text,
        "independent digest/member/reference verification plus strict offline GA re-evaluation",
        "transported archive verifies without reading original host evidence paths",
    )
    add(
        "ga_protected_publication",
        "GA_PUBLICATION_AUTHORIZED" in publication_authorizer_text
        and "allow_legacy_unbound=False" in publication_authorizer_text
        and "rerun the complete gate" in publication_authorizer_text
        and "environment: ga-production-publication" in publication_workflow_text
        and "if: github.repository == 'BaiKudan/DuckDock'" in publication_workflow_text
        and "Reconfirm the archived final-tag CI run succeeded"
        in publication_workflow_text
        and "gh release upload v2.0.0" in publication_workflow_text
        and "gh release edit v2.0.0 --draft=false" in publication_workflow_text
        and publication_workflow_text.index("authorize_ga_publication.py")
        < publication_workflow_text.index("gh release upload v2.0.0")
        < publication_workflow_text.index("gh release edit v2.0.0 --draft=false"),
        "protected draft plus independent archive digest/full re-evaluation/CI-run confirmation/immutable receipt",
        "only a recently authorized closure-bound archive can publish the final GitHub Release",
    )

    if shutil.which("kubectl"):
        rendered = _run(["kubectl", "kustomize", "ops/kubernetes/ha"])
    else:
        rendered = "\n---\n".join(
            (REPO_ROOT / "ops/kubernetes/ha" / name).read_text(encoding="utf-8")
            for name in ("workloads.yaml", "availability.yaml", "network-policies.yaml", "kustomization.yaml")
        )
    for kind in ("PodDisruptionBudget", "HorizontalPodAutoscaler", "NetworkPolicy", "Ingress"):
        add(
            f"ha_{kind.lower()}",
            f"kind: {kind}" in rendered,
            rendered.count(f"kind: {kind}"),
            f">=1 {kind}",
        )
    for deployment in ("backend", "frontend", "worker"):
        marker = f"name: {deployment}"
        offset = rendered.find(marker)
        section = rendered[offset : offset + 800] if offset >= 0 else ""
        add(
            f"ha_{deployment}_replicas",
            "replicas: 3" in section,
            "replicas=3" if "replicas: 3" in section else "missing",
            "3 baseline replicas",
        )
    add(
        "ha_cross_zone_spread",
        rendered.count("topology.kubernetes.io/zone") >= 3,
        rendered.count("topology.kubernetes.io/zone"),
        ">=3 zone spread constraints",
    )
    add(
        "ha_fault_domain_outage_scheduling",
        rendered.count("nodeTaintsPolicy: Honor") >= 3
        and rendered.count("matchLabelKeys:") >= 3
        and rendered.count("pod-template-hash") >= 3,
        (
            f"taint-aware={rendered.count('nodeTaintsPolicy: Honor')}, "
            f"revision-aware={rendered.count('pod-template-hash')}"
        ),
        ">=3 taint-aware and ReplicaSet-revision-aware zone constraints",
    )
    add(
        "ha_no_service_account_tokens",
        rendered.count("automountServiceAccountToken: false") >= 5,
        rendered.count("automountServiceAccountToken: false"),
        "all workloads disable service-account token automount",
    )
    add(
        "ha_template_requires_target_substitution",
        "example.invalid" in rendered and "sha256:0000000000000000" in rendered and "0.0.0.0/0" in rendered,
        "fail-closed placeholders retained",
        "target must replace image/domain/egress placeholders before authorization",
    )
    backend_dockerfile = (REPO_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    frontend_dockerfile = (REPO_ROOT / "frontend/Dockerfile.prod").read_text(
        encoding="utf-8"
    )
    add(
        "ha_numeric_non_root_images",
        "USER 10001:10001" in backend_dockerfile
        and "USER 101:101" in frontend_dockerfile,
        "backend=10001:10001, frontend=101:101",
        "numeric non-root image users accepted by Kubernetes runAsNonRoot",
    )
    rehearsal_script = (REPO_ROOT / "scripts/rehearse-kubernetes-ha.sh").read_text(
        encoding="utf-8"
    )
    kubernetes_nginx = (
        REPO_ROOT / "ops/kubernetes/ha-rehearsal/frontend-kubernetes.conf"
    ).read_text(encoding="utf-8")
    add(
        "ha_local_rehearsal_fail_closed",
        '"scope": "local-rehearsal"' in rehearsal_script
        and '"enforcement_exercised": False' in rehearsal_script
        and '"managed_mysql_ha": False' in rehearsal_script
        and "resolver 127.0.0.11" not in kubernetes_nginx
        and "backend.duckdock.svc.cluster.local" in kubernetes_nginx,
        "local-only scope; state/policy non-authorizing; Kubernetes Service proxy",
        "repeatable local rehearsal cannot be mistaken for target-production evidence",
    )

    passed = all(check.passed for check in checks)
    return {
        "schema_version": "duckdock-production-baseline-v1",
        "status": "PASS" if passed else "BLOCK",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "pass_count": sum(check.passed for check in checks),
        "block_count": sum(not check.passed for check in checks),
        "checks": [{**asdict(check), "status": "PASS" if check.passed else "BLOCK"} for check in checks],
        "authorization_scope": "repository baseline only; target substitution and fault injection remain mandatory",
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env.prod.example")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = verify(args.env_file.resolve())
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Production baseline verification failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
