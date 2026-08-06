"""Strict release-build provenance contracts shared by collector and GA gate."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BUILD_REPORT_SCHEMA_VERSION = "duckdock-ga-build-provenance-report-v1"
PROVENANCE_EVIDENCE_SCHEMA_VERSION = "duckdock-ga-release-provenance-evidence-v1"
PROVENANCE_POLICY_SCHEMA_VERSION = "duckdock-ga-release-provenance-trust-policy-v1"
VULNERABILITY_REPORT_SCHEMA_VERSION = "duckdock-ga-image-vulnerability-scan-v1"
BUILD_SIGNATURE_NAMESPACE = "duckdock-release-build-provenance"
SLSA_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
SPDX_VERSION = "SPDX-2.3"
SPDX_PREDICATE_TYPE = "https://spdx.dev/Document"
SARIF_VERSION = "2.1.0"
REQUIRED_ARTIFACTS = ("backend", "frontend")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^(?P<repository>[^\s@]+)@sha256:(?P<digest>[0-9a-f]{64})$")
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")


def meaningful(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(marker in value for marker in PLACEHOLDER_MARKERS)
    )


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def contains_secret_material_key(value: Any) -> bool:
    forbidden = {
        "access_key",
        "api_key",
        "authorization",
        "client_secret",
        "credential",
        "credential_value",
        "password",
        "password_value",
        "plaintext",
        "private_key",
        "raw_secret",
        "secret_value",
        "session_token",
        "token",
        "token_value",
    }
    if isinstance(value, dict):
        return any(
            str(key).lower() in forbidden or contains_secret_material_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_secret_material_key(item) for item in value)
    return False


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _resolve_artifact(raw: Any, report_path: Path) -> Path | None:
    if not meaningful(raw):
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = (report_path.parent / path).resolve()
    return path


def _content_addressed_file(reference: Any, report_path: Path, label: str) -> Path:
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise ValueError(f"{label} reference is invalid")
    path = _resolve_artifact(reference.get("path"), report_path)
    expected = str(reference.get("sha256", ""))
    if (
        path is None
        or not path.is_file()
        or not DIGEST_RE.fullmatch(expected)
        or sha256(path) != expected
    ):
        raise ValueError(f"{label} is missing or digest-mismatched")
    return path


def _image_subject_digest(subjects: Any, image: str) -> str | None:
    match = IMAGE_RE.fullmatch(image)
    if not (
        match is not None
        and isinstance(subjects, list)
        and len(subjects) == 1
        and isinstance(subjects[0], dict)
        and set(subjects[0]) == {"name", "digest"}
        and (
            subjects[0].get("name") == match.group("repository")
            or str(subjects[0].get("name", "")).startswith(
                f"pkg:docker/{match.group('repository')}@"
            )
        )
        and subjects[0].get("digest") == {"sha256": match.group("digest")}
    ):
        return None
    return match.group("digest")


def _validate_slsa_statement(
    path: Path,
    *,
    registry_predicate_path: Path,
    image: str,
    source_repository: str,
    git_commit: str,
    git_ref: str,
    builder_id: str,
    workflow_run_id: str,
    build_invocation_id: str,
    component: str,
    build_started_at: datetime,
    build_finished_at: datetime,
) -> dict[str, Any]:
    statement = _load_object(path, "SLSA provenance statement")
    registry_predicate = _load_object(
        registry_predicate_path, "registry BuildKit SLSA predicate"
    )
    predicate = statement.get("predicate")
    build_definition = (
        predicate.get("buildDefinition") if isinstance(predicate, dict) else None
    )
    run_details = predicate.get("runDetails") if isinstance(predicate, dict) else None
    external = (
        build_definition.get("externalParameters")
        if isinstance(build_definition, dict)
        else None
    )
    dependencies = (
        build_definition.get("resolvedDependencies")
        if isinstance(build_definition, dict)
        else None
    )
    builder = run_details.get("builder") if isinstance(run_details, dict) else None
    metadata = run_details.get("metadata") if isinstance(run_details, dict) else None
    config_source = external.get("configSource") if isinstance(external, dict) else None
    started_on = parse_time(metadata.get("startedOn")) if isinstance(metadata, dict) else None
    finished_on = parse_time(metadata.get("finishedOn")) if isinstance(metadata, dict) else None
    invocation_id = (
        metadata.get("invocationId", metadata.get("invocationID"))
        if isinstance(metadata, dict)
        else None
    )
    subject_digest = _image_subject_digest(statement.get("subject"), image)

    def source_reference_matches(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        uri = value.get("uri")
        digest = value.get("digest")
        if not isinstance(uri, str) or not isinstance(digest, dict):
            return False
        repository_part, separator, fragment = uri.partition("#")
        normalized_repository = repository_part.removesuffix(".git")
        return (
            separator == "#"
            and normalized_repository == source_repository.removesuffix(".git")
            and fragment in {git_ref, f"{git_ref}:{component}"}
            and (digest.get("sha1") == git_commit or digest.get("gitCommit") == git_commit)
        )

    config_source_matches = source_reference_matches(config_source)
    dependencies_valid = dependencies is None or (
        isinstance(dependencies, list)
        and all(isinstance(item, dict) for item in dependencies)
    )
    source_dependencies = (
        [
            item
            for item in dependencies
            if isinstance(item, dict)
            and isinstance(item.get("uri"), str)
            and str(item["uri"]).partition("#")[0].removesuffix(".git")
            == source_repository.removesuffix(".git")
        ]
        if isinstance(dependencies, list)
        else []
    )
    source_dependencies_match = all(
        source_reference_matches(item) for item in source_dependencies
    )
    expected_dockerfile = "Dockerfile.prod" if component == "frontend" else "Dockerfile"
    config_path = config_source.get("path") if isinstance(config_source, dict) else None
    config_path_matches = config_path in {
        expected_dockerfile,
        f"{component}/{expected_dockerfile}",
    }
    if not (
        set(statement) == {"_type", "subject", "predicateType", "predicate"}
        and statement.get("_type") == SLSA_STATEMENT_TYPE
        and statement.get("predicateType") == SLSA_PREDICATE_TYPE
        and predicate == registry_predicate
        and subject_digest is not None
        and isinstance(predicate, dict)
        and set(predicate) == {"buildDefinition", "runDetails"}
        and isinstance(build_definition, dict)
        and {"buildType", "externalParameters", "internalParameters"}.issubset(
            build_definition
        )
        and set(build_definition).issubset(
            {
                "buildType",
                "externalParameters",
                "internalParameters",
                "resolvedDependencies",
            }
        )
        and build_definition.get("buildType")
        == "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md"
        and isinstance(build_definition.get("internalParameters"), dict)
        and isinstance(external, dict)
        and config_source_matches
        and config_path_matches
        and dependencies_valid
        and source_dependencies_match
        and isinstance(run_details, dict)
        and {"builder", "metadata"}.issubset(run_details)
        and set(run_details).issubset({"builder", "metadata", "byproducts"})
        and isinstance(builder, dict)
        and builder.get("id") == builder_id
        and (
            "byproducts" not in run_details
            or isinstance(run_details.get("byproducts"), list)
        )
        and isinstance(metadata, dict)
        and invocation_id == build_invocation_id
        and started_on == build_started_at
        and finished_on == build_finished_at
        and started_on < finished_on
        and meaningful(workflow_run_id)
        and not contains_secret_material_key(statement)
    ):
        raise ValueError("SLSA statement does not bind the exact release source, builder and image")
    return {
        "sha256": sha256(path),
        "registry_predicate_sha256": sha256(registry_predicate_path),
        "predicate_type": SLSA_PREDICATE_TYPE,
        "subject_sha256": subject_digest or "invalid",
    }


def _validate_spdx_statement(
    path: Path,
    *,
    registry_document_path: Path,
    image: str,
) -> dict[str, Any]:
    statement = _load_object(path, "SPDX SBOM statement")
    registry_document = _load_object(registry_document_path, "registry SPDX SBOM")
    document = statement.get("predicate")
    subject_digest = _image_subject_digest(statement.get("subject"), image)
    if not (
        set(statement) == {"_type", "subject", "predicateType", "predicate"}
        and statement.get("_type") == SLSA_STATEMENT_TYPE
        and statement.get("predicateType") == SPDX_PREDICATE_TYPE
        and subject_digest is not None
        and document == registry_document
        and isinstance(document, dict)
    ):
        raise ValueError("SPDX statement does not bind the exact registry SBOM and image")
    packages = document.get("packages")
    creation = document.get("creationInfo")
    if not (
        document.get("spdxVersion") == SPDX_VERSION
        and document.get("dataLicense") == "CC0-1.0"
        and document.get("SPDXID") == "SPDXRef-DOCUMENT"
        and meaningful(document.get("name"))
        and meaningful(document.get("documentNamespace"))
        and isinstance(creation, dict)
        and parse_time(creation.get("created")) is not None
        and isinstance(creation.get("creators"), list)
        and bool(creation["creators"])
        and all(meaningful(item) for item in creation["creators"])
        and isinstance(packages, list)
        and bool(packages)
        and all(
            isinstance(package, dict)
            and meaningful(package.get("name"))
            and meaningful(package.get("SPDXID"))
            for package in packages
        )
        and not contains_secret_material_key(document)
    ):
        raise ValueError("SPDX SBOM is incomplete")
    return {
        "sha256": sha256(path),
        "registry_document_sha256": sha256(registry_document_path),
        "spdx_version": SPDX_VERSION,
        "predicate_type": SPDX_PREDICATE_TYPE,
        "subject_sha256": subject_digest,
        "created_at": creation["created"],
        "package_count": len(packages),
    }


def _validate_scout_sarif(path: Path) -> dict[str, Any]:
    report = _load_object(path, "Docker Scout SARIF report")
    runs = report.get("runs")
    if not (
        report.get("version") == SARIF_VERSION
        and isinstance(runs, list)
        and bool(runs)
        and not contains_secret_material_key(report)
    ):
        raise ValueError("Docker Scout SARIF report is incomplete")

    tools: list[tuple[str, str]] = []
    result_count = 0
    for run in runs:
        tool = run.get("tool") if isinstance(run, dict) else None
        driver = tool.get("driver") if isinstance(tool, dict) else None
        results = run.get("results", []) if isinstance(run, dict) else None
        version = (
            driver.get("semanticVersion", driver.get("version"))
            if isinstance(driver, dict)
            else None
        )
        if not (
            isinstance(driver, dict)
            and meaningful(driver.get("name"))
            and meaningful(version)
            and isinstance(results, list)
            and all(isinstance(result, dict) for result in results)
        ):
            raise ValueError("Docker Scout SARIF run is malformed")
        tools.append((driver["name"], version))
        result_count += len(results)
    if result_count:
        raise ValueError("Docker Scout SARIF still contains Critical/High results")
    return {
        "sha256": sha256(path),
        "version": SARIF_VERSION,
        "run_count": len(runs),
        "result_count": result_count,
        "tools": tools,
    }


def _validate_vulnerability_report(
    path: Path,
    *,
    scout_sarif_path: Path,
    image: str,
) -> dict[str, Any]:
    report = _load_object(path, "vulnerability scan report")
    sarif = _validate_scout_sarif(scout_sarif_path)
    scanner = report.get("scanner")
    findings = report.get("findings")
    summary = report.get("summary")
    if not (
        set(report) == {
            "schema_version",
            "image",
            "scanner",
            "scanned_at",
            "severity_filter",
            "raw_sarif_sha256",
            "raw_sarif_result_count",
            "findings",
            "summary",
        }
        and report.get("schema_version") == VULNERABILITY_REPORT_SCHEMA_VERSION
        and report.get("image") == image
        and isinstance(scanner, dict)
        and set(scanner) == {"name", "version", "database_updated_at"}
        and meaningful(scanner.get("name"))
        and meaningful(scanner.get("version"))
        and (scanner.get("name"), scanner.get("version")) in sarif["tools"]
        and parse_time(scanner.get("database_updated_at")) is not None
        and parse_time(report.get("scanned_at")) is not None
        and report.get("severity_filter") == ["critical", "high"]
        and report.get("raw_sarif_sha256") == sarif["sha256"]
        and report.get("raw_sarif_result_count") == sarif["result_count"]
        and isinstance(findings, list)
        and isinstance(summary, dict)
        and set(summary) == {"open_critical", "open_high", "total_findings"}
        and not contains_secret_material_key(report)
    ):
        raise ValueError("vulnerability report is incomplete or cross-image")
    severities = {"critical", "high", "medium", "low", "unknown"}
    statuses = {"open", "fixed"}
    if not all(
        isinstance(item, dict)
        and set(item) == {
            "id",
            "severity",
            "package",
            "installed_version",
            "fixed_version",
            "status",
        }
        and meaningful(item.get("id"))
        and item.get("severity") in severities
        and meaningful(item.get("package"))
        and meaningful(item.get("installed_version"))
        and isinstance(item.get("fixed_version"), str)
        and item.get("status") in statuses
        for item in findings
    ):
        raise ValueError("vulnerability findings are malformed")
    open_critical = sum(
        item["severity"] == "critical" and item["status"] == "open" for item in findings
    )
    open_high = sum(
        item["severity"] == "high" and item["status"] == "open" for item in findings
    )
    if summary != {
        "open_critical": open_critical,
        "open_high": open_high,
        "total_findings": len(findings),
    } or open_critical or open_high or findings or sarif["result_count"]:
        raise ValueError("vulnerability summary is forged or Critical/High remains open")
    return {
        "sha256": sha256(path),
        "raw_sarif_sha256": sarif["sha256"],
        "raw_sarif_run_count": sarif["run_count"],
        "raw_sarif_result_count": 0,
        "scanner": scanner,
        "scanned_at": report["scanned_at"],
        "open_critical": 0,
        "open_high": 0,
        "total_findings": len(findings),
    }


def validate_build_report(
    report: Any,
    *,
    report_path: Path,
    git_commit: str,
    backend_image: str,
    frontend_image: str,
    contract_digest: str,
    source_repository: str,
    approved_builder_ids: set[str],
    approved_workflow_refs: set[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("build provenance report must be an object")
    expected_keys = {
        "schema_version",
        "release_version",
        "git_ref",
        "git_commit",
        "tag_object",
        "tag_signature_verified",
        "tag_verification_output",
        "source_repository",
        "source_tree_sha256",
        "source_archive",
        "contract_digest",
        "build_started_at",
        "build_finished_at",
        "builder",
        "artifacts",
    }
    builder = report.get("builder")
    artifacts = report.get("artifacts")
    archive = report.get("source_archive")
    started_at = parse_time(report.get("build_started_at"))
    finished_at = parse_time(report.get("build_finished_at"))
    current = now or datetime.now(timezone.utc)
    if not (
        set(report) == expected_keys
        and report.get("schema_version") == BUILD_REPORT_SCHEMA_VERSION
        and report.get("release_version") == "2.0.0"
        and report.get("git_ref") == "refs/tags/v2.0.0"
        and report.get("git_commit") == git_commit
        and bool(COMMIT_RE.fullmatch(git_commit))
        and isinstance(report.get("tag_object"), dict)
        and set(report["tag_object"]) == {"path", "sha256"}
        and report.get("tag_signature_verified") is True
        and isinstance(report.get("tag_verification_output"), dict)
        and set(report["tag_verification_output"]) == {"path", "sha256"}
        and report.get("source_repository") == source_repository
        and bool(DIGEST_RE.fullmatch(str(report.get("source_tree_sha256", ""))))
        and isinstance(archive, dict)
        and set(archive) == {"path", "sha256", "size_bytes"}
        and bool(DIGEST_RE.fullmatch(str(archive.get("sha256", ""))))
        and isinstance(archive.get("size_bytes"), int)
        and not isinstance(archive.get("size_bytes"), bool)
        and archive["size_bytes"] > 0
        and report.get("contract_digest") == contract_digest
        and bool(DIGEST_RE.fullmatch(contract_digest))
        and started_at is not None
        and finished_at is not None
        and started_at < finished_at <= current
        and isinstance(builder, dict)
        and set(builder) == {
            "builder_id",
            "workflow_ref",
            "workflow_run_id",
            "workflow_run_url",
            "build_invocation_id",
        }
        and builder.get("builder_id") in approved_builder_ids
        and builder.get("workflow_ref") in approved_workflow_refs
        and meaningful(builder.get("workflow_run_id"))
        and meaningful(builder.get("workflow_run_url"))
        and meaningful(builder.get("build_invocation_id"))
        and isinstance(artifacts, dict)
        and set(artifacts) == set(REQUIRED_ARTIFACTS)
        and not contains_secret_material_key(report)
    ):
        raise ValueError("build report does not bind the exact signed 2.0.0 source and builder")
    tag_object_path = _content_addressed_file(
        report["tag_object"], report_path, "signed v2.0.0 tag object"
    )
    tag_verification_path = _content_addressed_file(
        report["tag_verification_output"], report_path, "v2.0.0 tag verification output"
    )
    try:
        tag_object_text = tag_object_path.read_text(encoding="utf-8")
        tag_verification_bytes = tag_verification_path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read signed tag evidence: {exc}") from exc
    try:
        parsed_tag_verification = json.loads(tag_verification_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        parsed_tag_verification = None
    verification_text = tag_verification_bytes.decode("utf-8", errors="replace")
    verification_passed = (
        isinstance(parsed_tag_verification, dict)
        and (
            parsed_tag_verification.get("verified") is True
            or (
                isinstance(parsed_tag_verification.get("verification"), dict)
                and parsed_tag_verification["verification"].get("verified") is True
            )
        )
    ) or any(
        marker in verification_text
        for marker in ("Good signature", "Good SSH signature", 'Good "git" signature')
    )
    if not (
        tag_object_path.stat().st_size <= 1024 * 1024
        and tag_verification_bytes
        and len(tag_verification_bytes) <= 1024 * 1024
        and verification_passed
        and f"object {git_commit}\n" in tag_object_text
        and "type commit\n" in tag_object_text
        and "tag v2.0.0\n" in tag_object_text
        and any(
            marker in tag_object_text
            for marker in ("-----BEGIN PGP SIGNATURE-----", "-----BEGIN SSH SIGNATURE-----")
        )
    ):
        raise ValueError("captured v2.0.0 tag is not an annotated signed tag for this commit")
    archive_path = _resolve_artifact(archive.get("path"), report_path)
    if not (
        archive_path is not None
        and archive_path.is_file()
        and sha256(archive_path) == archive.get("sha256")
        and archive_path.stat().st_size == archive.get("size_bytes")
    ):
        raise ValueError("source archive is missing or does not match its digest and size")
    expected_images = {"backend": backend_image, "frontend": frontend_image}
    projections: dict[str, Any] = {}
    for name in REQUIRED_ARTIFACTS:
        artifact = artifacts[name]
        image = expected_images[name]
        if not (
            isinstance(artifact, dict)
            and set(artifact)
            == {
                "image",
                "attestation_image",
                "registry_provenance",
                "slsa",
                "registry_sbom",
                "sbom",
                "scout_sarif",
                "vulnerability_scan",
            }
            and artifact.get("image") == image
            and IMAGE_RE.fullmatch(image)
            and IMAGE_RE.fullmatch(str(artifact.get("attestation_image", "")))
            and str(artifact["attestation_image"]).rsplit("@sha256:", maxsplit=1)[0]
            == image.rsplit("@sha256:", maxsplit=1)[0]
        ):
            raise ValueError(f"{name} artifact does not bind the authorized image")
        registry_provenance_path = _content_addressed_file(
            artifact["registry_provenance"],
            report_path,
            f"{name} registry provenance",
        )
        slsa_path = _content_addressed_file(artifact["slsa"], report_path, f"{name} SLSA")
        registry_sbom_path = _content_addressed_file(
            artifact["registry_sbom"],
            report_path,
            f"{name} registry SBOM",
        )
        sbom_path = _content_addressed_file(artifact["sbom"], report_path, f"{name} SBOM")
        scout_sarif_path = _content_addressed_file(
            artifact["scout_sarif"],
            report_path,
            f"{name} Docker Scout SARIF",
        )
        scan_path = _content_addressed_file(
            artifact["vulnerability_scan"],
            report_path,
            f"{name} vulnerability scan",
        )
        slsa = _validate_slsa_statement(
            slsa_path,
            registry_predicate_path=registry_provenance_path,
            image=image,
            source_repository=source_repository,
            git_commit=git_commit,
            git_ref="refs/tags/v2.0.0",
            builder_id=builder["builder_id"],
            workflow_run_id=builder["workflow_run_id"],
            build_invocation_id=builder["build_invocation_id"],
            component=name,
            build_started_at=started_at,
            build_finished_at=finished_at,
        )
        sbom = _validate_spdx_statement(
            sbom_path,
            registry_document_path=registry_sbom_path,
            image=image,
        )
        scan = _validate_vulnerability_report(
            scan_path,
            scout_sarif_path=scout_sarif_path,
            image=image,
        )
        sbom_created_at = parse_time(sbom["created_at"])
        scan_time = parse_time(scan["scanned_at"])
        database_time = parse_time(scan["scanner"]["database_updated_at"])
        if not (
            sbom_created_at is not None
            and scan_time is not None
            and database_time is not None
            and started_at <= sbom_created_at <= scan_time <= current
            and finished_at <= scan_time <= finished_at + timedelta(hours=24)
            and database_time <= scan_time
            and scan_time - database_time <= timedelta(days=7)
        ):
            raise ValueError(f"{name} SBOM and vulnerability scan timeline is invalid")
        projections[name] = {
            "image": image,
            "attestation_image": artifact["attestation_image"],
            "slsa": slsa,
            "sbom": sbom,
            "vulnerability_scan": scan,
        }
    return {
        "built_at": finished_at,
        "builder": builder,
        "source_repository": source_repository,
        "git_ref": "refs/tags/v2.0.0",
        "git_commit": git_commit,
        "source_tree_sha256": report["source_tree_sha256"],
        "source_archive": archive,
        "contract_digest": contract_digest,
        "artifacts": projections,
    }
