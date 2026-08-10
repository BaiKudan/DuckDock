"""Campaign-bound dual-control authorization for risky GA target execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.ga_path_resolution import ga_file_resolution_override
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_path_resolution import ga_file_resolution_override


CAMPAIGN_SCHEMA_VERSION = "duckdock-ga-execution-campaign-v5"
MANIFEST_SCHEMA_VERSION = "duckdock-ga-execution-authorization-manifest-v1"
STATEMENT_SCHEMA_VERSION = "duckdock-ga-execution-authorization-statement-v1"
VERIFICATION_SCHEMA_VERSION = "duckdock-ga-execution-authorization-verification-v1"
APPROVAL_POLICY_SCHEMA_VERSION = "duckdock-ga-approval-policy-v2"
SIGNATURE_NAMESPACE = "duckdock-ga-execution-authorization"
AUTHORIZATION_BOUNDARY = "authorizes_only_named_campaign_phases_not_GA_or_unlisted_mutation"
CAMPAIGN_BOUNDARY = "does_not_authorize_GA_or_target_mutation"
REQUIRED_SIGNER_ROLES = ("Operations", "Security")
REQUIRED_APPROVAL_ROLES = {"Product", "Architecture", "Security", "Operations"}
AUTHORIZATION_ID_RE = re.compile(r"^gaexecauth_[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")
MAXIMUM_CLOCK_SKEW = timedelta(minutes=5)


def meaningful(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(marker in value for marker in PLACEHOLDER_MARKERS)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_payload(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonical_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def atomic_write(path: Path, payload: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError(f"output already exists: {path}") from exc
        temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def resolve_recorded_path(raw: Any, *, base: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty path")
    handled, overridden = ga_file_resolution_override(raw)
    if handled:
        if overridden is None:
            raise ValueError(f"{label} has no verified portable file mapping")
        candidate = overridden
    else:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = base.parent / candidate
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link")
    path = candidate.resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def checked_reference(value: Any, *, base: Path, label: str) -> tuple[Path, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError(f"{label} must contain exactly path and sha256")
    digest = value.get("sha256")
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise ValueError(f"{label}.sha256 must be SHA-256")
    path = resolve_recorded_path(value.get("path"), base=base, label=label)
    if sha256(path) != digest:
        raise ValueError(f"{label} digest mismatch")
    return path, digest


def _exact_identity_list(value: Any, *, label: str) -> list[str]:
    if not (
        isinstance(value, list)
        and value
        and all(meaningful(identity) for identity in value)
        and len(value) == len(set(value))
    ):
        raise ValueError(f"{label} must contain unique non-placeholder identities")
    return [str(identity) for identity in value]


def _allowed_signer_bindings(path: Path) -> dict[str, set[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read release-authority allowed-signers: {exc}") from exc
    bindings: dict[str, set[str]] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        key_index = next(
            (index for index, field in enumerate(fields[1:], start=1) if field.startswith(("ssh-", "ecdsa-", "sk-"))),
            None,
        )
        if key_index is None or key_index + 1 >= len(fields):
            raise ValueError(f"release-authority allowed-signers line {line_number} has no OpenSSH public key")
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if not meaningful(principal) or any(character in principal for character in "*?!"):
                raise ValueError(f"release-authority allowed-signers line {line_number} must use exact principals")
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        raise ValueError("release-authority allowed-signers contains no principals")
    key_owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            key_owners.setdefault(key, set()).add(principal)
    if any(len(owners) != 1 for owners in key_owners.values()):
        raise ValueError("release-authority allowed-signers reuses a public key across identities")
    return bindings


def load_approval_authority(
    campaign_path: Path,
    campaign: dict[str, Any],
    *,
    expected_policy_path: Path | None = None,
) -> tuple[dict[str, Any], Path, Path, dict[str, set[str]]]:
    topology = campaign.get("trust_topology")
    if not isinstance(topology, dict):
        raise ValueError("execution campaign has no trust topology")
    receipt_path, _ = checked_reference(topology.get("receipt"), base=campaign_path, label="trust topology receipt")
    receipt = load_object(receipt_path, "trust topology receipt")
    if not (
        receipt.get("schema_version") == "duckdock-ga-trust-topology-verification-v1"
        and receipt.get("status") == "PASS"
    ):
        raise ValueError("trust topology receipt is not a PASS verification")
    policies = receipt.get("policies")
    approval = policies.get("approval") if isinstance(policies, dict) else None
    if not isinstance(approval, dict):
        raise ValueError("trust topology receipt has no approval authority")
    policy_path, policy_digest = checked_reference(approval.get("policy"), base=receipt_path, label="approval policy")
    trust_path, trust_digest = checked_reference(
        approval.get("allowed_signers"),
        base=receipt_path,
        label="approval allowed-signers",
    )
    if expected_policy_path is not None and policy_path != expected_policy_path.resolve():
        raise ValueError("explicit approval policy differs from campaign trust topology")
    policy = load_object(policy_path, "approval policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "roles",
        "independent_security_assessors",
    }
    roles = policy.get("roles")
    if not isinstance(roles, dict) or set(roles) != REQUIRED_APPROVAL_ROLES:
        raise ValueError("approval policy must contain the four required roles")
    role_identities = {
        str(role): set(_exact_identity_list(identities, label=f"approval role {role}"))
        for role, identities in roles.items()
    }
    assessors = policy.get("independent_security_assessors")
    if not isinstance(assessors, dict) or not assessors:
        raise ValueError("approval policy must contain independent security assessors")
    assessor_identities: set[str] = set()
    for provider, identities in assessors.items():
        if not meaningful(provider):
            raise ValueError("approval policy assessor provider is invalid")
        configured = _exact_identity_list(identities, label=f"approval assessor provider {provider}")
        if assessor_identities.intersection(configured):
            raise ValueError("approval policy reuses an assessor identity")
        assessor_identities.update(configured)
    all_role_identities = set().union(*role_identities.values())
    if len(all_role_identities) != sum(len(items) for items in role_identities.values()):
        raise ValueError("approval policy reuses an identity across organizational roles")
    if all_role_identities.intersection(assessor_identities):
        raise ValueError("approval policy reuses an approver identity as an assessor")
    configured_identities = all_role_identities | assessor_identities
    policy_trust_path = resolve_recorded_path(
        policy.get("allowed_signers_path"),
        base=policy_path,
        label="approval policy allowed-signers",
    )
    bindings = _allowed_signer_bindings(policy_trust_path)
    if not (
        set(policy) == expected_keys
        and policy.get("schema_version") == APPROVAL_POLICY_SCHEMA_VERSION
        and meaningful(policy.get("policy_id"))
        and meaningful(policy.get("organization"))
        and policy_digest == sha256(policy_path)
        and policy_trust_path == trust_path
        and policy.get("allowed_signers_sha256") == trust_digest
        and sha256(trust_path) == trust_digest
        and set(bindings) == configured_identities
    ):
        raise ValueError("campaign approval authority is not exact or content-addressed")
    return policy, policy_path, trust_path, role_identities


def authorized_phases(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    phases = campaign.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("execution campaign has no phases")
    selected: list[dict[str, Any]] = []
    for phase in phases:
        if not isinstance(phase, dict):
            raise ValueError("execution campaign phase is invalid")
        acknowledgement = phase.get("required_acknowledgement")
        if not meaningful(acknowledgement):
            continue
        dependencies = phase.get("depends_on")
        if not isinstance(dependencies, list) or "execution_authorization" not in dependencies:
            raise ValueError(f"risky execution phase {phase.get('phase_id')} is not authorization-gated")
        phase_id = phase.get("phase_id")
        roles = phase.get("authorized_policy_roles")
        tools = phase.get("tools")
        outputs = phase.get("outputs")
        if not (
            meaningful(phase_id)
            and meaningful(phase.get("risk_class"))
            and isinstance(roles, list)
            and isinstance(tools, list)
            and tools
            and all(meaningful(tool) for tool in tools)
            and isinstance(outputs, list)
            and outputs
            and all(meaningful(output) for output in outputs)
        ):
            raise ValueError(f"risky execution phase {phase_id} has invalid controls")
        selected.append(
            {
                "phase_id": phase_id,
                "risk_class": phase["risk_class"],
                "depends_on": list(dependencies),
                "required_acknowledgement": acknowledgement,
                "authorized_policy_roles": list(roles),
                "tools": list(tools),
                "outputs": list(outputs),
            }
        )
    if not selected:
        raise ValueError("execution campaign has no explicitly acknowledged risky phases")
    return selected


def validate_campaign(campaign_path: Path) -> dict[str, Any]:
    if campaign_path.is_symlink() or not campaign_path.is_file():
        raise ValueError("execution campaign must be a regular non-symlink file")
    campaign_path = campaign_path.resolve()
    campaign = load_object(campaign_path, "execution campaign")
    if not (
        campaign.get("schema_version") == CAMPAIGN_SCHEMA_VERSION
        and campaign.get("status") == "PLANNED_EXTERNAL_EXECUTION"
        and campaign.get("authorization_boundary") == CAMPAIGN_BOUNDARY
        and meaningful(campaign.get("campaign_id"))
        and isinstance(campaign.get("release"), dict)
        and isinstance(campaign.get("target"), dict)
        and isinstance(campaign.get("execution"), dict)
    ):
        raise ValueError("execution campaign is not an exact non-authorizing campaign")
    created_at = parse_time(campaign.get("created_at"), "campaign created_at")
    starts_at = parse_time(campaign.get("window_starts_at"), "campaign window_starts_at")
    expires_at = parse_time(campaign.get("window_expires_at"), "campaign window_expires_at")
    if not created_at < starts_at < expires_at:
        raise ValueError("execution campaign timeline is invalid")
    authorized_phases(campaign)
    request_path, _ = checked_reference(
        campaign.get("request"), base=campaign_path, label="execution campaign request"
    )
    topology = campaign.get("trust_topology")
    if not isinstance(topology, dict):
        raise ValueError("execution campaign has no trust topology")
    topology_receipt_path, _ = checked_reference(
        topology.get("receipt"), base=campaign_path, label="trust topology receipt"
    )
    assembly_request_path, _ = checked_reference(
        campaign.get("preapproval_assembly_request"),
        base=campaign_path,
        label="preapproval assembly request",
    )
    request_reference = campaign.get("request")
    portable_resolution, _ = ga_file_resolution_override(
        request_reference.get("path")
        if isinstance(request_reference, dict)
        else None
    )
    if not portable_resolution:
        try:
            from scripts.prepare_ga_execution_campaign import prepare
        except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
            from prepare_ga_execution_campaign import prepare

        evidence_root = Path(str(campaign["execution"].get("evidence_root", "")))
        regenerated_campaign, regenerated_request = prepare(
            request_path,
            topology_receipt_path,
            assembly_request_path,
            now=created_at,
            require_fresh_evidence_root=not evidence_root.exists(),
        )
        if regenerated_campaign != campaign:
            raise ValueError("execution campaign did not independently re-verify")
        if load_object(assembly_request_path, "preapproval assembly request") != regenerated_request:
            raise ValueError("preapproval assembly request did not independently re-verify")
    return campaign


def prepare_manifest(
    campaign_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign = validate_campaign(campaign_path)
    campaign_path = campaign_path.resolve()
    created_at = parse_time(campaign["created_at"], "campaign created_at")
    starts_at = parse_time(campaign["window_starts_at"], "campaign window_starts_at")
    if current < created_at:
        raise ValueError("execution authorization cannot predate campaign creation")
    if current >= starts_at:
        raise ValueError("execution authorization manifest must be prepared before the window starts")
    body = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "prepared_at": current.isoformat(),
        "campaign": {"path": str(campaign_path), "sha256": sha256(campaign_path)},
        "release": campaign["release"],
        "target": campaign["target"],
        "execution": campaign["execution"],
        "authorized_phases": authorized_phases(campaign),
        "required_signer_roles": list(REQUIRED_SIGNER_ROLES),
    }
    return {**body, "authorization_id": f"gaexecauth_{canonical_digest(body)}"}


def validate_manifest(
    campaign_path: Path,
    manifest_path: Path,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if campaign_path.is_symlink() or not campaign_path.is_file():
        raise ValueError("execution campaign must be a regular non-symlink file")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("execution authorization manifest must be a regular non-symlink file")
    campaign_path = campaign_path.resolve()
    manifest_path = manifest_path.resolve()
    campaign = validate_campaign(campaign_path)
    manifest = load_object(manifest_path, "execution authorization manifest")
    artifacts = campaign.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("execution campaign has no planned artifacts")
    planned_manifest_path = resolve_recorded_path(
        artifacts.get("execution_authorization_manifest"),
        base=campaign_path,
        label="planned execution authorization manifest",
    )
    expected_keys = {
        "schema_version",
        "authorization_id",
        "authorization_boundary",
        "prepared_at",
        "campaign",
        "release",
        "target",
        "execution",
        "authorized_phases",
        "required_signer_roles",
    }
    if set(manifest) != expected_keys:
        raise ValueError("execution authorization manifest has invalid fields")
    referenced_campaign, campaign_digest = checked_reference(
        manifest.get("campaign"), base=manifest_path, label="authorized campaign"
    )
    body = {key: value for key, value in manifest.items() if key != "authorization_id"}
    prepared_at = parse_time(manifest.get("prepared_at"), "authorization prepared_at")
    created_at = parse_time(campaign.get("created_at"), "campaign created_at")
    starts_at = parse_time(campaign.get("window_starts_at"), "campaign window_starts_at")
    expected_id = f"gaexecauth_{canonical_digest(body)}"
    if not (
        manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
        and manifest.get("authorization_boundary") == AUTHORIZATION_BOUNDARY
        and AUTHORIZATION_ID_RE.fullmatch(str(manifest.get("authorization_id", "")))
        and manifest.get("authorization_id") == expected_id
        and manifest_path == planned_manifest_path
        and referenced_campaign == campaign_path
        and campaign_digest == sha256(campaign_path)
        and manifest.get("release") == campaign.get("release")
        and manifest.get("target") == campaign.get("target")
        and manifest.get("execution") == campaign.get("execution")
        and manifest.get("authorized_phases") == authorized_phases(campaign)
        and manifest.get("required_signer_roles") == list(REQUIRED_SIGNER_ROLES)
        and created_at <= prepared_at < starts_at
        and prepared_at <= current + MAXIMUM_CLOCK_SKEW
    ):
        raise ValueError("execution authorization manifest does not match its campaign or timeline")
    return campaign, manifest


def acknowledgement_projection(manifest: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "phase_id": str(phase["phase_id"]),
            "acknowledgement": str(phase["required_acknowledgement"]),
        }
        for phase in manifest["authorized_phases"]
    ]


def create_statement(
    campaign_path: Path,
    manifest_path: Path,
    *,
    role: str,
    identity: str,
    approval_policy_path: Path,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign, manifest = validate_manifest(campaign_path, manifest_path, now=current)
    _, _, trust_path, role_identities = load_approval_authority(
        campaign_path.resolve(),
        campaign,
        expected_policy_path=approval_policy_path,
    )
    if role not in REQUIRED_SIGNER_ROLES or identity not in role_identities[role]:
        raise ValueError("approval policy does not authorize this exact execution signer role")
    prepared_at = parse_time(manifest["prepared_at"], "authorization prepared_at")
    starts_at = parse_time(campaign["window_starts_at"], "campaign window_starts_at")
    expires_at = parse_time(campaign["window_expires_at"], "campaign window_expires_at")
    if current > expires_at:
        raise ValueError("execution authorization campaign has expired")
    if current < prepared_at or current >= starts_at:
        raise ValueError("execution authorization must be signed after preparation and before window start")
    return (
        {
            "schema_version": STATEMENT_SCHEMA_VERSION,
            "authorization_id": manifest["authorization_id"],
            "authorization_boundary": AUTHORIZATION_BOUNDARY,
            "campaign_id": campaign["campaign_id"],
            "campaign_sha256": sha256(campaign_path.resolve()),
            "manifest": {
                "path": str(manifest_path.resolve()),
                "sha256": sha256(manifest_path.resolve()),
            },
            "role": role,
            "identity": identity,
            "decision": "APPROVE",
            "signed_at": current.isoformat(),
            "acknowledgements": acknowledgement_projection(manifest),
        },
        trust_path,
    )


def sign_payload(payload: bytes, *, key: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="duckdock-ga-execution-authorization-") as root:
        statement_path = Path(root) / "statement.json"
        statement_path.write_bytes(payload)
        try:
            completed = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "sign",
                    "-f",
                    str(key),
                    "-n",
                    SIGNATURE_NAMESPACE,
                    str(statement_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        except OSError as exc:
            raise ValueError(f"cannot execute ssh-keygen: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
            raise ValueError(f"cannot sign execution authorization: {detail}")
        signature_path = Path(f"{statement_path}.sig")
        if not signature_path.is_file():
            raise ValueError("ssh-keygen did not create an execution authorization signature")
        return signature_path.read_bytes()


def verify_signature(
    statement_path: Path,
    signature_path: Path,
    *,
    identity: str,
    allowed_signers: Path,
) -> None:
    try:
        completed = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(allowed_signers),
                "-I",
                identity,
                "-n",
                SIGNATURE_NAMESPACE,
                "-s",
                str(signature_path),
            ],
            input=statement_path.read_bytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot verify execution authorization signature: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid execution authorization signature: {detail}")


def verify_authorization(
    campaign_path: Path,
    manifest_path: Path,
    statements: Mapping[str, tuple[Path, Path]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign, manifest = validate_manifest(campaign_path, manifest_path, now=current)
    campaign_path = campaign_path.resolve()
    manifest_path = manifest_path.resolve()
    artifacts = campaign.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("execution campaign has no planned artifacts")
    policy, policy_path, trust_path, role_identities = load_approval_authority(campaign_path, campaign)
    if set(statements) != set(REQUIRED_SIGNER_ROLES):
        raise ValueError("execution authorization requires exact Security and Operations statements")
    prepared_at = parse_time(manifest["prepared_at"], "authorization prepared_at")
    starts_at = parse_time(campaign["window_starts_at"], "campaign window_starts_at")
    expires_at = parse_time(campaign["window_expires_at"], "campaign window_expires_at")
    if current > expires_at:
        raise ValueError("execution authorization campaign has expired")
    expected_keys = {
        "schema_version",
        "authorization_id",
        "authorization_boundary",
        "campaign_id",
        "campaign_sha256",
        "manifest",
        "role",
        "identity",
        "decision",
        "signed_at",
        "acknowledgements",
    }
    signer_records: dict[str, dict[str, Any]] = {}
    signer_identities: set[str] = set()
    tracked_inputs = {
        campaign_path: sha256(campaign_path),
        manifest_path: sha256(manifest_path),
        policy_path: sha256(policy_path),
        trust_path: sha256(trust_path),
    }
    for role in REQUIRED_SIGNER_ROLES:
        raw_statement_path, raw_signature_path = statements[role]
        if raw_statement_path.is_symlink() or not raw_statement_path.is_file():
            raise ValueError(f"{role} execution authorization statement is missing or symlinked")
        if raw_signature_path.is_symlink() or not raw_signature_path.is_file():
            raise ValueError(f"{role} execution authorization signature is missing or symlinked")
        statement_path, signature_path = (
            raw_statement_path.resolve(),
            raw_signature_path.resolve(),
        )
        tracked_inputs[statement_path] = sha256(statement_path)
        tracked_inputs[signature_path] = sha256(signature_path)
        slug = role.lower()
        planned_statement_path = resolve_recorded_path(
            artifacts.get(f"execution_authorization_{slug}_statement"),
            base=campaign_path,
            label=f"planned {role} execution authorization statement",
        )
        planned_signature_path = resolve_recorded_path(
            artifacts.get(f"execution_authorization_{slug}_signature"),
            base=campaign_path,
            label=f"planned {role} execution authorization signature",
        )
        statement = load_object(statement_path, f"{role} execution authorization statement")
        referenced_manifest, manifest_digest = checked_reference(
            statement.get("manifest"), base=statement_path, label=f"{role} authorized manifest"
        )
        identity = statement.get("identity")
        signed_at = parse_time(statement.get("signed_at"), f"{role} authorization signed_at")
        if not (
            set(statement) == expected_keys
            and statement_path == planned_statement_path
            and signature_path == planned_signature_path
            and statement.get("schema_version") == STATEMENT_SCHEMA_VERSION
            and statement.get("authorization_id") == manifest.get("authorization_id")
            and statement.get("authorization_boundary") == AUTHORIZATION_BOUNDARY
            and statement.get("campaign_id") == campaign.get("campaign_id")
            and statement.get("campaign_sha256") == sha256(campaign_path)
            and referenced_manifest == manifest_path
            and manifest_digest == sha256(manifest_path)
            and statement.get("role") == role
            and isinstance(identity, str)
            and identity in role_identities[role]
            and statement.get("decision") == "APPROVE"
            and statement.get("acknowledgements") == acknowledgement_projection(manifest)
            and prepared_at <= signed_at < starts_at
            and signed_at <= current + MAXIMUM_CLOCK_SKEW
        ):
            raise ValueError(f"{role} execution authorization statement is invalid or out of window")
        if identity in signer_identities:
            raise ValueError("Security and Operations execution authorization identities must differ")
        verify_signature(
            statement_path,
            signature_path,
            identity=identity,
            allowed_signers=trust_path,
        )
        signer_identities.add(identity)
        signer_records[role] = {
            "identity": identity,
            "signed_at": signed_at.isoformat(),
            "statement": {
                "path": str(statement_path),
                "sha256": tracked_inputs[statement_path],
            },
            "signature": {
                "path": str(signature_path),
                "sha256": tracked_inputs[signature_path],
            },
        }
    final_campaign, final_manifest = validate_manifest(
        campaign_path,
        manifest_path,
        now=current,
    )
    final_policy, final_policy_path, final_trust_path, final_role_identities = (
        load_approval_authority(campaign_path, final_campaign)
    )
    if not (
        final_campaign == campaign
        and final_manifest == manifest
        and final_policy == policy
        and final_policy_path == policy_path
        and final_trust_path == trust_path
        and final_role_identities == role_identities
        and all(sha256(path) == digest for path, digest in tracked_inputs.items())
    ):
        raise ValueError("execution authorization inputs changed during verification")
    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "status": "AUTHORIZED_FOR_NAMED_PHASE_EXECUTION",
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "authorization_id": manifest["authorization_id"],
        "campaign_id": campaign["campaign_id"],
        "manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path)},
        "approval_policy": {
            "policy_id": policy["policy_id"],
            "path": str(policy_path),
            "sha256": sha256(policy_path),
        },
        "allowed_signers": {"path": str(trust_path), "sha256": sha256(trust_path)},
        "authorized_phase_ids": [phase["phase_id"] for phase in manifest["authorized_phases"]],
        "validity": {
            "starts_at": starts_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
        "signers": signer_records,
        "verified_at": current.isoformat(),
    }
