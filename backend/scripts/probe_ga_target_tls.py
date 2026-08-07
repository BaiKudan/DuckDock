#!/usr/bin/env python3
"""Probe the real DuckDock application and object-store TLS endpoints."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

try:
    from scripts.ga_tls_evidence import (
        EXERCISE_RE,
        TLS_RAW_SCHEMA_VERSION,
        canonical_digest,
        globally_routable_ip,
    )
    from scripts.ga_release_identity import EVIDENCE_SCOPES, build_release_binding
    from scripts.ga_execution_phase_start import verify_runtime_entry
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_tls_evidence import (
        EXERCISE_RE,
        TLS_RAW_SCHEMA_VERSION,
        canonical_digest,
        globally_routable_ip,
    )
    from ga_release_identity import EVIDENCE_SCOPES, build_release_binding
    from ga_execution_phase_start import verify_runtime_entry


HSTS_MAX_AGE_RE = re.compile(r"(?:^|;)\s*max-age=(\d+)", re.IGNORECASE)


def _endpoint(raw: str) -> tuple[str, int, str]:
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"HTTPS URL required: {raw}")
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return parsed.hostname, port, path


def _context(ca_file: Path | None, version: ssl.TLSVersion) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    context.minimum_version = version
    context.maximum_version = version
    return context


def _certificate_name(value: Any) -> str:
    if not isinstance(value, tuple):
        return ""
    return ",".join(
        f"{key}={item}"
        for relative_name in value
        if isinstance(relative_name, tuple)
        for key, item in relative_name
        if isinstance(key, str) and isinstance(item, str)
    )


def _handshake(
    host: str,
    port: int,
    *,
    version: ssl.TLSVersion,
    ca_file: Path | None,
    timeout: float,
) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw_socket:
            with _context(ca_file, version).wrap_socket(raw_socket, server_hostname=host) as tls_socket:
                certificate = tls_socket.getpeercert()
                certificate_der = tls_socket.getpeercert(binary_form=True)
                captured_at = datetime.now(timezone.utc)
                not_before = certificate.get("notBefore")
                not_after = certificate.get("notAfter")
                starts_at = (
                    datetime.fromtimestamp(ssl.cert_time_to_seconds(not_before), timezone.utc)
                    if isinstance(not_before, str)
                    else None
                )
                expires_at = (
                    datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), timezone.utc)
                    if isinstance(not_after, str)
                    else None
                )
                return {
                    "ok": True,
                    "captured_at": captured_at.isoformat(),
                    "protocol": tls_socket.version(),
                    "cipher": tls_socket.cipher()[0] if tls_socket.cipher() else None,
                    "peer_ip": str(tls_socket.getpeername()[0]),
                    "peer_certificate_sha256": hashlib.sha256(certificate_der).hexdigest(),
                    "certificate_serial_number": certificate.get("serialNumber"),
                    "certificate_subject": _certificate_name(certificate.get("subject")),
                    "certificate_issuer": _certificate_name(certificate.get("issuer")),
                    "certificate_not_before": starts_at.isoformat() if starts_at else None,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                    "certificate_days_remaining": (
                        int((expires_at - captured_at).total_seconds() // 86400)
                        if expires_at
                        else -1
                    ),
                    "hostname_verified": True,
                }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _http_probe(
    host: str,
    port: int,
    path: str,
    *,
    ca_file: Path | None,
    timeout: float,
) -> dict[str, Any]:
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    connection = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    try:
        connection.request("GET", path, headers={"User-Agent": "DuckDock-GA-TLS-Probe/1"})
        response = connection.getresponse()
        body_prefix = response.read(4096)
        response_headers = [
            {"name": name.lower(), "value": value}
            for name, value in response.getheaders()
        ]
        hsts = response.getheader("Strict-Transport-Security", "")
        match = HSTS_MAX_AGE_RE.search(hsts)
        return {
            "ok": 200 <= response.status < 400,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status_code": response.status,
            "hsts": hsts,
            "hsts_max_age_seconds": int(match.group(1)) if match else 0,
            "response_headers": response_headers,
            "response_headers_sha256": canonical_digest(response_headers),
            "body_prefix_bytes": len(body_prefix),
            "body_prefix_sha256": hashlib.sha256(body_prefix).hexdigest(),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "hsts_max_age_seconds": 0}
    finally:
        connection.close()


def _legacy_protocol_rejected(host: str, port: int, *, flag: str, timeout: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "openssl",
                "s_client",
                "-connect",
                f"{host}:{port}",
                "-servername",
                host,
                flag,
                "-brief",
            ],
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"rejected": False, "error": f"{type(exc).__name__}: {exc}"}
    output = completed.stdout[:16_384].decode("utf-8", errors="replace")
    negotiated = completed.returncode == 0 and "Protocol version:" in output
    return {
        "rejected": not negotiated,
        "openssl_exit_code": completed.returncode,
        "result": "negotiated" if negotiated else "rejected",
        "raw_output": output,
        "raw_output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


def probe(args: argparse.Namespace) -> dict[str, Any]:
    endpoints = {
        "application": _endpoint(args.app_url),
        "object_store": _endpoint(args.object_store_url),
    }
    results: dict[str, Any] = {}
    protocols: set[str] = set()
    certificate_days: list[int] = []
    hostname_verified = True
    hsts_values: list[int] = []
    legacy_protocols_rejected: set[str] = set()
    passed = True
    for name, (host, port, path) in endpoints.items():
        handshakes: dict[str, Any] = {}
        for label, version in (("TLSv1.2", ssl.TLSVersion.TLSv1_2), ("TLSv1.3", ssl.TLSVersion.TLSv1_3)):
            result = _handshake(
                host,
                port,
                version=version,
                ca_file=args.ca_file,
                timeout=args.timeout,
            )
            handshakes[label] = result
            if result.get("ok"):
                protocols.add(str(result["protocol"]))
                certificate_days.append(int(result["certificate_days_remaining"]))
                hostname_verified = hostname_verified and result.get("hostname_verified") is True
        http_result = _http_probe(
            host,
            port,
            path,
            ca_file=args.ca_file,
            timeout=args.timeout,
        )
        hsts_values.append(int(http_result.get("hsts_max_age_seconds", 0)))
        legacy = {
            "TLSv1": _legacy_protocol_rejected(host, port, flag="-tls1", timeout=args.timeout),
            "TLSv1.1": _legacy_protocol_rejected(host, port, flag="-tls1_1", timeout=args.timeout),
        }
        for protocol, result in legacy.items():
            if result.get("rejected") is True:
                legacy_protocols_rejected.add(protocol)
        endpoint_ok = (
            handshakes["TLSv1.2"].get("ok") is True
            and handshakes["TLSv1.3"].get("ok") is True
            and http_result.get("ok") is True
            and all(item.get("rejected") is True for item in legacy.values())
        )
        passed = passed and endpoint_ok
        results[name] = {
            "host": host,
            "port": port,
            "path": path,
            "handshakes": handshakes,
            "http": http_result,
            "legacy_protocols": legacy,
            "passed": endpoint_ok,
        }
    minimum_days = min(certificate_days, default=-1)
    minimum_hsts = min(hsts_values, default=0)
    passed = passed and minimum_days >= args.minimum_certificate_days and minimum_hsts >= args.minimum_hsts_max_age
    return {
        "schema_version": TLS_RAW_SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASS" if passed else "BLOCK",
        "passed": passed,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "exercise_id": args.exercise_id,
        "probe": {
            "probe_id": args.probe_id,
            "vantage_id": args.vantage_id,
            "vantage_class": "external-internet",
            "source_ip": args.source_ip,
        },
        "application_url": args.app_url,
        "object_store_url": args.object_store_url,
        "negotiated_protocols": sorted(protocols),
        "certificate_days_remaining": minimum_days,
        "hostname_verified": hostname_verified,
        "legacy_protocols_rejected": sorted(legacy_protocols_rejected),
        "hsts_max_age_seconds": minimum_hsts,
        "minimum_certificate_days": args.minimum_certificate_days,
        "minimum_hsts_max_age_seconds": args.minimum_hsts_max_age,
        "endpoints": results,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-url", required=True, help="application health URL")
    parser.add_argument("--object-store-url", required=True, help="object-store health URL")
    parser.add_argument("--scope", choices=sorted(EVIDENCE_SCOPES), default="target-production")
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--execution-campaign", type=Path)
    parser.add_argument("--phase-action-id")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--probe-id", required=True)
    parser.add_argument("--vantage-id", required=True)
    parser.add_argument("--source-ip", required=True)
    parser.add_argument("--acknowledge-external-vantage", required=True)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--minimum-certificate-days", type=int, default=30)
    parser.add_argument("--minimum-hsts-max-age", type=int, default=31_536_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if not EXERCISE_RE.fullmatch(args.exercise_id):
            raise ValueError("exercise ID must be 8-64 safe characters")
        if args.acknowledge_external_vantage != args.target_environment:
            raise ValueError(
                "--acknowledge-external-vantage must exactly match --target-environment"
            )
        if args.scope == "target-production" and not globally_routable_ip(args.source_ip):
            raise ValueError("target-production TLS probe source IP must be globally routable")
        if not args.probe_id.strip() or not args.vantage_id.strip():
            raise ValueError("probe and vantage IDs must be non-empty")
        args.release_binding = build_release_binding(
            scope=args.scope,
            target_environment=args.target_environment,
            source_commit=args.source_commit,
            backend_image=args.backend_image,
            frontend_image=args.frontend_image,
        )
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.scope == "target-production":
            if args.execution_campaign is None or not args.phase_action_id:
                raise ValueError(
                    "target-production TLS probes require --execution-campaign and --phase-action-id"
                )
            verify_runtime_entry(
                args.execution_campaign,
                phase_id="tls",
                action_id=args.phase_action_id,
                release_binding=args.release_binding,
                target_environment=args.target_environment,
            )
        result = probe(args)
    except ValueError as exc:
        print(f"TLS probe error: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
