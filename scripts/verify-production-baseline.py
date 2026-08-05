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
