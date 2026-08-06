from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.collect_ga_independent_security as security_collector
from scripts.ga_release_identity import build_release_binding
from scripts.ga_security_assessment import (
    ASSESSMENT_REPORT_SCHEMA_VERSION,
    ASSESSMENT_SIGNATURE_NAMESPACE,
    FINDING_SEVERITIES,
    REQUIRED_ASSESSMENT_SCOPE,
)


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
CONTRACT_DIGEST = "d" * 64
TARGET = "customer-production"
PROVIDER = "Independent Security Lab"
ASSESSOR_IDENTITY = "assessor@independent-security.example"


def _generate_key(path: Path) -> None:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
        check=True,
    )


def _sign(path: Path, key: Path) -> Path:
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            ASSESSMENT_SIGNATURE_NAMESPACE,
            str(path),
        ],
        check=True,
    )
    return Path(f"{path}.sig")


def _material(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for security assessment signature tests")
    keys: dict[str, Path] = {}
    identities = {
        "Product": "product@example.com",
        "Architecture": "architecture@example.com",
        "Security": "security@example.com",
        "Operations": "operations@example.com",
    }
    for role, identity in identities.items():
        key = tmp_path / f"{role.lower()}_key"
        _generate_key(key)
        keys[identity] = key
    assessor_key = tmp_path / "assessor_key"
    _generate_key(assessor_key)
    keys[ASSESSOR_IDENTITY] = assessor_key
    trust_path = tmp_path / "release-authority.allowed-signers"
    trust_path.write_text(
        "".join(
            f"{identity} {key.with_suffix('.pub').read_text(encoding='utf-8').strip()}\n"
            for identity, key in keys.items()
        ),
        encoding="utf-8",
    )
    policy = {
        "schema_version": security_collector.APPROVAL_POLICY_SCHEMA_VERSION,
        "policy_id": "duckdock-production-release-authority",
        "organization": "DuckDock Test Release Authority",
        "allowed_signers_path": str(trust_path),
        "allowed_signers_sha256": hashlib.sha256(trust_path.read_bytes()).hexdigest(),
        "roles": {role: [identity] for role, identity in identities.items()},
        "independent_security_assessors": {PROVIDER: [ASSESSOR_IDENTITY]},
    }
    policy_path = tmp_path / "approval-policy.json"
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    artifact_path = tmp_path / "duckdock-2.0-assessment.pdf"
    artifact_path.write_bytes(b"%PDF-1.7\nIndependent DuckDock assessment\n%%EOF\n")
    report_path = tmp_path / "assessment.json"
    report_path.write_text(
        json.dumps(_report(artifact_path), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    signature_path = _sign(report_path, assessor_key)
    return policy_path, report_path, signature_path, artifact_path, assessor_key


def _counts() -> dict[str, int]:
    return {severity: 0 for severity in FINDING_SEVERITIES}


def _report(artifact_path: Path, *, open_high: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    findings = []
    open_counts = _counts()
    if open_high:
        findings.append(
            {
                "finding_id": "DD-SEC-001",
                "severity": "high",
                "status": "open",
                "affected_components": ["backend-api"],
                "discovered_at": (now - timedelta(days=2)).isoformat(),
                "retested_at": None,
            }
        )
        open_counts["high"] = 1
    return {
        "schema_version": ASSESSMENT_REPORT_SCHEMA_VERSION,
        "assessment_id": "ISL-DD-2026-08",
        "provider": PROVIDER,
        "assessor_identity": ASSESSOR_IDENTITY,
        "independence": {
            "independent_of_implementation": True,
            "conflict_check_completed": True,
            "implementation_contributors": 0,
        },
        "target_environment": TARGET,
        "source_commit": COMMIT,
        "images": {
            "backend": {"name": BACKEND_IMAGE},
            "frontend": {"name": FRONTEND_IMAGE},
        },
        "contract_digest": CONTRACT_DIGEST,
        "scope": list(REQUIRED_ASSESSMENT_SCOPE),
        "methodologies": ["penetration-test", "manual-code-review", "dependency-analysis"],
        "started_at": (now - timedelta(days=14)).isoformat(),
        "completed_at": (now - timedelta(days=1)).isoformat(),
        "findings": findings,
        "summary": {
            "total_findings": len(findings),
            "open_by_severity": open_counts,
            "closed_by_severity": _counts(),
            "critical_high_retest_completed": not open_high,
        },
        "report_artifact": {
            "path": str(artifact_path),
            "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            "format": "pdf",
        },
        "completed": True,
    }


def _args(
    tmp_path: Path,
    policy_path: Path,
    report_path: Path,
    signature_path: Path,
) -> SimpleNamespace:
    return SimpleNamespace(
        target_environment=TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        contract_digest=CONTRACT_DIGEST,
        provider=PROVIDER,
        assessor_signer_identity=ASSESSOR_IDENTITY,
        assessment_report=report_path,
        assessment_signature=signature_path,
        approval_policy=policy_path,
        output=tmp_path / "security-evidence.json",
        release_binding=build_release_binding(
            scope="target-production",
            target_environment=TARGET,
            source_commit=COMMIT,
            backend_image=BACKEND_IMAGE,
            frontend_image=FRONTEND_IMAGE,
        ),
    )


def test_collects_policy_bound_signed_security_assessment(tmp_path: Path) -> None:
    policy_path, report_path, signature_path, _, _ = _material(tmp_path)

    evidence = security_collector.collect(
        _args(tmp_path, policy_path, report_path, signature_path)
    )

    assert evidence["status"] == "PASS"
    assert evidence["open_critical"] == 0
    assert evidence["open_high"] == 0
    assert evidence["signed_assessment"]["assessor_identity"] == ASSESSOR_IDENTITY
    assert "allowed_signers_path" not in evidence["signed_assessment"]["signed_evidence"]


def test_rejects_signature_from_key_not_in_release_authority(tmp_path: Path) -> None:
    policy_path, report_path, signature_path, _, _ = _material(tmp_path)
    signature_path.unlink()
    attacker_key = tmp_path / "attacker_key"
    _generate_key(attacker_key)
    attacker_signature = _sign(report_path, attacker_key)

    with pytest.raises(ValueError, match="invalid independent assessment signature"):
        security_collector.collect(
            _args(tmp_path, policy_path, report_path, attacker_signature)
        )


def test_rejects_assessor_not_authorized_for_provider(tmp_path: Path) -> None:
    policy_path, report_path, signature_path, _, _ = _material(tmp_path)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["independent_security_assessors"] = {
        "Different Security Lab": [ASSESSOR_IDENTITY]
    }
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="does not authorize this independent assessor"):
        security_collector.collect(
            _args(tmp_path, policy_path, report_path, signature_path)
        )


def test_rejects_assessor_public_key_reused_by_internal_approver(tmp_path: Path) -> None:
    policy_path, report_path, signature_path, _, assessor_key = _material(tmp_path)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    trust_path = Path(policy["allowed_signers_path"])
    assessor_public = assessor_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    lines = trust_path.read_text(encoding="utf-8").splitlines()
    lines[0] = f"product@example.com {assessor_public}"
    trust_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    policy["allowed_signers_sha256"] = hashlib.sha256(trust_path.read_bytes()).hexdigest()
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="reuses a public key across identities"):
        security_collector.collect(
            _args(tmp_path, policy_path, report_path, signature_path)
        )


def test_rejects_signed_summary_that_hides_raw_high_finding(tmp_path: Path) -> None:
    policy_path, report_path, signature_path, artifact_path, assessor_key = _material(tmp_path)
    signature_path.unlink()
    report = _report(artifact_path, open_high=True)
    report["summary"]["open_by_severity"]["high"] = 0
    report["summary"]["critical_high_retest_completed"] = True
    report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    signature_path = _sign(report_path, assessor_key)

    with pytest.raises(ValueError, match="summary does not match raw findings"):
        security_collector.collect(
            _args(tmp_path, policy_path, report_path, signature_path)
        )


def test_rejects_report_artifact_changed_after_assessor_signature(tmp_path: Path) -> None:
    policy_path, report_path, signature_path, artifact_path, _ = _material(tmp_path)
    artifact_path.write_bytes(b"%PDF-1.7\ntampered after assessment signing\n%%EOF\n")

    with pytest.raises(ValueError, match="artifact is missing or digest-mismatched"):
        security_collector.collect(
            _args(tmp_path, policy_path, report_path, signature_path)
        )


def test_open_high_finding_produces_blocked_evidence(tmp_path: Path) -> None:
    policy_path, report_path, signature_path, artifact_path, assessor_key = _material(tmp_path)
    signature_path.unlink()
    report_path.write_text(
        json.dumps(_report(artifact_path, open_high=True), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    signature_path = _sign(report_path, assessor_key)

    evidence = security_collector.collect(
        _args(tmp_path, policy_path, report_path, signature_path)
    )

    assert evidence["status"] == "BLOCKED"
    assert evidence["passed"] is False
    assert evidence["open_high"] == 1
