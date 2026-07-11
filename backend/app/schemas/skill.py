from datetime import datetime
from dataclasses import dataclass
import re

import yaml
from pydantic import BaseModel, field_validator, model_validator

from app.core.identifiers import normalize_resource_identifier
from app.models.scan import IssueSeverity, ScanStatus
from app.models.public_release import PublicReleaseApprovalStatus
from app.models.skill import SkillVersionReviewStatus, SkillVersionStatus


# ── SKILL.md 验证 ─────────────────────────────────────────────────

REQUIRED_SKILL_MD_FIELDS = {"name", "description", "version"}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
SKILL_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


@dataclass
class SkillPackageValidationResult:
    files: dict[str, str]
    metadata: dict
    warnings: list[str]
    validation_spec: dict | None = None


def normalize_package_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip().strip("/")
    if not normalized:
        raise ValueError("File path cannot be empty")
    if normalized.startswith(".git/") or normalized == ".git" or "/.git/" in normalized:
        raise ValueError("Package files cannot target .git paths")
    if ".." in normalized.split("/"):
        raise ValueError(f"Package file path '{normalized}' cannot contain '..'")
    return "SKILL.md" if normalized.lower() == "skill.md" else normalized


def parse_skill_front_matter(text: str) -> dict:
    text = text.lstrip("\ufeff")
    metadata: dict = {}
    if not text.startswith("---"):
        return metadata
    lines = text.split("\n")
    in_front = False
    for line in lines:
        if line.strip() == "---":
            if not in_front:
                in_front = True
                continue
            break
        if in_front and ":" in line:
            k, _, v = line.partition(":")
            metadata[k.strip()] = v.strip()
    return metadata


def extract_markdown_body(text: str) -> str:
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return text.strip()
    lines = text.split("\n")
    in_front = False
    front_closed = False
    body_lines: list[str] = []
    for line in lines:
        if line.strip() == "---":
            if not in_front:
                in_front = True
                continue
            if not front_closed:
                front_closed = True
                continue
        if front_closed:
            body_lines.append(line)
    return "\n".join(body_lines).strip()


def validate_skill_md(metadata: dict, *, require_version: bool = True) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors: list[str] = []
    required_fields = {"name", "description", "version"} if require_version else {"name", "description"}
    for field in required_fields:
        if field not in metadata or not metadata[field]:
            errors.append(f"SKILL.md missing required field: '{field}'")
    if "name" in metadata and metadata.get("name") and not SKILL_IDENTIFIER_RE.match(str(metadata["name"])):
        errors.append("SKILL.md 'name' must be lower_snake_case and start with a letter")
    if "version" in metadata and metadata.get("version"):
        v = metadata["version"].lstrip("v")
        if not SEMVER_RE.match(v):
            errors.append(
                f"SKILL.md 'version' must be semver (e.g. 1.0.0), got: {metadata['version']}"
            )
    return errors


def validate_skill_package(
    package_files: dict[str, str],
    *,
    expected_version: str | None = None,
    require_version: bool = True,
) -> SkillPackageValidationResult:
    normalized_files: dict[str, str] = {}
    warnings: list[str] = []
    for path, content in package_files.items():
        normalized = normalize_package_path(path)
        if normalized in normalized_files:
            raise ValueError(f"Duplicate package file path after normalization: '{normalized}'")
        normalized_files[normalized] = content

    skill_md = normalized_files.get("SKILL.md")
    if not skill_md:
        raise ValueError("SKILL.md required at the package root")
    body = extract_markdown_body(skill_md)
    if not body:
        raise ValueError("SKILL.md must include body content after front-matter")

    metadata = parse_skill_front_matter(skill_md)
    if expected_version:
        normalized_version = expected_version.lstrip("v")
        declared = str(metadata.get("version") or "").strip()
        if declared:
            if declared.lstrip("v") != normalized_version:
                raise ValueError(
                    f"SKILL.md version '{declared}' does not match published version '{normalized_version}'"
                )
        else:
            metadata["version"] = normalized_version
            warnings.append("Injected SKILL.md version from published package version")
    errors = validate_skill_md(metadata, require_version=require_version)
    if errors:
        raise ValueError("; ".join(errors))

    validation_spec = None
    validation_yaml = normalized_files.get(".duckdock/validation.yaml")
    if validation_yaml:
        try:
            parsed_validation = yaml.safe_load(validation_yaml) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f".duckdock/validation.yaml is not valid YAML: {exc}") from exc
        if not isinstance(parsed_validation, dict):
            raise ValueError(".duckdock/validation.yaml must contain a YAML object at the top level")
        checks = parsed_validation.get("checks")
        if checks is not None and not isinstance(checks, list):
            raise ValueError(".duckdock/validation.yaml 'checks' must be a list")
        smoke_prompts = parsed_validation.get("smoke_prompts")
        if smoke_prompts is not None and not isinstance(smoke_prompts, list):
            raise ValueError(".duckdock/validation.yaml 'smoke_prompts' must be a list")
        if isinstance(smoke_prompts, list):
            for index, prompt in enumerate(smoke_prompts, start=1):
                if not isinstance(prompt, dict):
                    raise ValueError(f".duckdock/validation.yaml smoke_prompts[{index}] must be an object")
                message = prompt.get("message")
                if not isinstance(message, str) or not message.strip():
                    raise ValueError(f".duckdock/validation.yaml smoke_prompts[{index}].message is required")
                for field in ("expect_contains", "expect_any_contains", "reject_contains"):
                    value = prompt.get(field)
                    if value is not None and not (
                        isinstance(value, list) and all(isinstance(item, str) for item in value)
                    ):
                        raise ValueError(
                            f".duckdock/validation.yaml smoke_prompts[{index}].{field} must be a list of strings"
                        )
                timeout_seconds = prompt.get("timeout_seconds")
                if timeout_seconds is not None and (
                    not isinstance(timeout_seconds, int) or timeout_seconds <= 0
                ):
                    raise ValueError(
                        f".duckdock/validation.yaml smoke_prompts[{index}].timeout_seconds must be a positive integer"
                    )
        validation_spec = parsed_validation

    return SkillPackageValidationResult(
        files=normalized_files,
        metadata=metadata,
        warnings=warnings,
        validation_spec=validation_spec,
    )


# ── Request / Response schemas ────────────────────────────────────

class SkillCreate(BaseModel):
    name: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return normalize_resource_identifier(v, "Skill name")


class SkillUpdate(BaseModel):
    description: str | None = None


class SkillOut(BaseModel):
    id: int
    namespace_id: int
    name: str
    description: str | None
    git_repo_path: str
    created_at: datetime
    deleted_at: datetime | None = None
    latest_tag: str | None = None
    version_count: int = 0

    model_config = {"from_attributes": True}


class PublishVersionRequest(BaseModel):
    """Files to commit as a new skill version."""
    tag: str                            # e.g. "v1.0.0"
    skill_md: str | None = None         # contents of SKILL.md (legacy)
    system_prompt: str | None = None    # contents of system_prompt.md
    extra_files: dict[str, str] | None = None
    package_files: dict[str, str] | None = None
    message: str | None = None          # commit message
    changelog: str | None = None
    publish_tags: list[str] | None = None
    share_public: bool = False
    license_name: str | None = None
    license_attested: bool = False
    risk_acknowledged: bool = False
    public_expires_in_days: int | None = None

    @field_validator("tag")
    @classmethod
    def validate_tag(cls, v: str) -> str:
        if not re.match(r"^v\d+\.\d+\.\d+$", v):
            raise ValueError("Tag must be semver with v prefix, e.g. v1.0.0")
        return v

    @field_validator("extra_files")
    @classmethod
    def validate_extra_files(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        cleaned: dict[str, str] = {}
        for path, content in v.items():
            normalized = normalize_package_path(path)
            if normalized in {"SKILL.md", "system_prompt.md"}:
                raise ValueError(f"Extra file path '{normalized}' is reserved")
            cleaned[normalized] = content
        return cleaned

    @field_validator("package_files")
    @classmethod
    def validate_package_files(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        cleaned: dict[str, str] = {}
        for path, content in v.items():
            normalized = normalize_package_path(path)
            cleaned[normalized] = content
        return cleaned

    @field_validator("publish_tags")
    @classmethod
    def validate_publish_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned: list[str] = []
        for item in v:
            value = str(item).strip().lower()
            if not value:
                continue
            if not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", value):
                raise ValueError("Publish tags must be lowercase alphanumeric tokens")
            cleaned.append(value)
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def validate_payload_shape(self):
        if self.package_files:
            return self
        if not self.skill_md:
            raise ValueError("Either package_files or skill_md is required")
        return self

    def to_package_files(self) -> dict[str, str]:
        if self.package_files:
            return dict(self.package_files)
        files = {"SKILL.md": self.skill_md or ""}
        if self.system_prompt:
            files["system_prompt.md"] = self.system_prompt
        if self.extra_files:
            files.update(self.extra_files)
        return files


class SkillGenerateDraftRequest(BaseModel):
    name: str
    description: str | None = None
    tag: str
    prompt: str
    create_if_missing: bool = True
    template_key: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return normalize_resource_identifier(v, "Skill name")

    @field_validator("tag")
    @classmethod
    def validate_generation_tag(cls, v: str) -> str:
        if not re.match(r"^v\d+\.\d+\.\d+$", v):
            raise ValueError("Tag must be semver with v prefix, e.g. v1.0.0")
        return v

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, v: str) -> str:
        value = v.strip()
        if len(value) < 10:
            raise ValueError("Prompt must be at least 10 characters")
        return value


class DraftValidationIssue(BaseModel):
    rule: str
    severity: IssueSeverity
    message: str
    file: str | None = None
    snippet: str | None = None
    line: int | None = None


class SkillGenerateDraftResponse(BaseModel):
    skill_name: str
    description: str | None
    tag: str
    skill_md: str
    system_prompt: str | None = None
    package_files: dict[str, str]
    changelog: str | None = None
    publish_tags: list[str] = []
    created_skill: bool = False
    validation_status: ScanStatus
    validation_errors: list[str]
    validation_issues: list[DraftValidationIssue]
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    ready_to_publish: bool
    model: str


class SkillVersionOut(BaseModel):
    id: int
    skill_id: int
    tag: str
    commit_sha: str
    status: SkillVersionStatus
    review_status: SkillVersionReviewStatus
    review_required: bool = False
    review_notes: str | None = None
    review_requested_at: datetime | None = None
    reviewed_at: datetime | None = None
    gate_result: dict | None = None
    skill_metadata: dict | None
    changelog: str | None = None
    publish_tags: list[str] | None = None
    content_fingerprint: str | None = None
    file_count: int = 0
    created_at: datetime
    is_public_shared: bool = False
    public_shared_at: datetime | None = None

    model_config = {"from_attributes": True}


class SkillVersionDetail(SkillVersionOut):
    files: dict[str, str]   # file path → content


class DiffResponse(BaseModel):
    from_tag: str
    to_tag: str
    diff: str


class SearchResult(BaseModel):
    items: list[SkillOut]
    total: int


class SkillVersionSharingUpdate(BaseModel):
    is_public_shared: bool
    license_name: str | None = None
    license_attested: bool = False
    risk_acknowledged: bool = False
    public_expires_in_days: int | None = None


class SkillVersionReviewUpdate(BaseModel):
    decision: str
    notes: str | None = None

    @field_validator("decision")
    @classmethod
    def validate_decision(cls, v: str) -> str:
        value = v.strip().lower()
        if value not in {"approve", "reject", "reset"}:
            raise ValueError("Decision must be approve, reject, or reset")
        return value


class SkillVersionSharingApprovalUpdate(BaseModel):
    approval_status: PublicReleaseApprovalStatus
    approval_notes: str | None = None


class SkillVersionSharingState(BaseModel):
    namespace: str
    skill: str
    tag: str
    is_public_shared: bool
    public_shared_at: datetime | None = None
    public_ready: bool
    approval_status: PublicReleaseApprovalStatus | None = None
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    requires_approval: bool = False
    license_name: str | None = None
    license_attested: bool = False
    risk_acknowledged: bool = False
    public_slug: str
    public_download_url: str
    public_inspect_url: str
