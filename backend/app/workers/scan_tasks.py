"""
Celery 扫描任务

调用链：
  publish_version endpoint
    → create ScanResult(PENDING)
    → trigger_scan.delay(version_id)
      → 设置状态为 RUNNING
      → 拉取 Git 版本文件
      → 静态扫描
      → (可选) AI 深度扫描(统一 DashScope/Qwen,解析出 key 时启用)
      → 写入扫描结果
      → 更新 SkillVersion.status
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import resolve_llm
from app.models.scan import ScanResult, ScanStatus
from app.models.sandbox_validation import SandboxValidationRun, SandboxValidationStatus
from app.models.scanner_suppression import ScannerRuleSuppression
from app.models.skill import SkillVersion, SkillVersionStatus, Skill
from app.models.namespace import Namespace
from app.services.public_release_service import get_public_release
from app.services.governance_service import get_or_create_namespace_governance
from app.services.release_gate_service import release_gate_service
from app.services.scanner_service import (
    scanner_service,
    aggregate_issues,
    apply_suppressions,
    build_ai_assist,
    ScanLLMError,
)
from app.services.sandbox_validation_service import sandbox_validation_service
from app.services.sync_event_service import derive_sync_state, record_version_sync_event
from app.services.webhook_service import dispatch_event
from app.services.git_service import git_service, GitServiceError
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session

logger = logging.getLogger(__name__)


def _resolve_scan_llm() -> tuple[str, str, str]:
    """解析深扫的 (api_key, base_url, model)。

    扫描没有专属 SCAN_LLM_* 前缀,直接复用规范 DUCKDOCK_LLM_*(L2-PROVIDER-UNIFY)。
    返回的 api_key 非空即视为深扫已闸控放行;为空则跳过深扫(仅静态)。
    """
    return resolve_llm("DUCKDOCK_LLM")


def _attach_ai_assist(gate_result: dict, ai_assist: dict) -> dict:
    """Surface the shared ai_assist indicator inside a release-gate result.

    The gate result is the durable, API-surfaced JSON for a version's scan/gate
    outcome. Record whether the AI deep scan actually ran under technical_checks so
    reviewers see it alongside scan_status/sandbox_status (no new migration needed).
    """
    technical_checks = dict(gate_result.get("technical_checks") or {})
    technical_checks["ai_assist"] = ai_assist
    gate_result["technical_checks"] = technical_checks
    return gate_result


@celery_app.task(name="scan_skill_version", bind=True, max_retries=2)
def trigger_scan(self, version_id: int):
    """Entry point — runs the async scan logic in a new event loop."""
    try:
        asyncio.run(_async_scan(version_id))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)


async def _async_scan(version_id: int):
    async with worker_db_session() as db:
        # Load version + scan result
        v_result = await db.execute(
            select(SkillVersion).where(SkillVersion.id == version_id)
        )
        version = v_result.scalar_one_or_none()
        if not version:
            return

        scan_result = await db.execute(
            select(ScanResult).where(ScanResult.version_id == version_id)
        )
        scan = scan_result.scalar_one_or_none()
        if not scan:
            scan = ScanResult(version_id=version_id)
            db.add(scan)
            await db.flush()

        sandbox_result = await db.execute(
            select(SandboxValidationRun).where(SandboxValidationRun.version_id == version_id)
        )
        sandbox_run = sandbox_result.scalar_one_or_none()
        if not sandbox_run:
            sandbox_run = SandboxValidationRun(version_id=version_id)
            db.add(sandbox_run)
            await db.flush()

        public_release = await get_public_release(db, version_id=version.id)
        previous_sync_state = derive_sync_state(version, public_release)

        # Mark as running
        scan.status = ScanStatus.RUNNING
        scan.started_at = datetime.now(timezone.utc)
        sandbox_run.status = SandboxValidationStatus.PENDING
        sandbox_run.summary = None
        sandbox_run.checks = None
        sandbox_run.logs = None
        sandbox_run.started_at = None
        sandbox_run.completed_at = None
        version.status = SkillVersionStatus.SCANNING
        await db.commit()

        try:
            # Resolve namespace + skill name for git_service
            skill_result = await db.execute(
                select(Skill).where(Skill.id == version.skill_id, Skill.deleted_at.is_(None))
            )
            skill = skill_result.scalar_one_or_none()
            ns_result = await db.execute(
                select(Namespace).where(
                    Namespace.id == skill.namespace_id,
                    Namespace.deleted_at.is_(None),
                )
            )
            ns = ns_result.scalar_one_or_none()
            if skill is None or ns is None:
                scan.status = ScanStatus.PENDING
                scan.started_at = None
                version.status = SkillVersionStatus.QUARANTINE
                await db.commit()
                return

            # Collect all skill names in this namespace for dependency check
            all_skills = await db.execute(
                select(Skill.name).where(Skill.namespace_id == ns.id, Skill.deleted_at.is_(None))
            )
            known_names = set(all_skills.scalars().all())

            # Get skill files from Git
            try:
                files = git_service.get_version_files(ns.name, skill.name, version.tag)
            except GitServiceError as e:
                await _fail_scan(db, scan, version, str(e))
                return

            policy = await get_or_create_namespace_governance(db, namespace_id=ns.id)

            # Run static scanner
            issues = scanner_service.scan_files(
                files=files,
                metadata=version.skill_metadata,
                known_skill_names=known_names,
            )

            # Optional: AI deep scan via the unified DashScope/Qwen provider.
            # Default-on intent, key-gated: runs whenever a unified scan key resolves;
            # with no key the deep scan is skipped and only static findings are kept.
            #
            # Surface the outcome via the shared ai_assist degradation indicator so
            # reviewers can tell whether AI scanning actually happened (mode="llm")
            # versus the static-only baseline (mode="baseline", degraded=true).
            scan_api_key, scan_base_url, scan_model = _resolve_scan_llm()
            if scan_api_key:
                try:
                    ai_issues = await scanner_service.scan_with_llm(
                        files,
                        api_key=scan_api_key,
                        base_url=scan_base_url,
                        model=scan_model,
                    )
                except ScanLLMError as exc:
                    # The deep scan was configured but did not actually run — record it
                    # as degraded (NOT mode="llm") so the ai_assist signal stays honest.
                    logger.warning("Deep scan failed; keeping static-only: %s", exc)
                    ai_assist = build_ai_assist(
                        deep_scan_ran=False, reason=f"deep_scan_failed: {exc}"
                    )
                else:
                    issues.extend(ai_issues)
                    ai_assist = build_ai_assist(deep_scan_ran=True)
            else:
                ai_assist = build_ai_assist(
                    deep_scan_ran=False, reason="no_llm_key_resolved"
                )

            # Drop issues covered by namespace-level suppressions before aggregating.
            suppression_rows = (
                await db.execute(
                    select(ScannerRuleSuppression.rule_id, ScannerRuleSuppression.file_pattern)
                    .where(ScannerRuleSuppression.namespace_id == ns.id)
                )
            ).all()
            issues, suppressed_records = apply_suppressions(
                issues, [(row[0], row[1]) for row in suppression_rows]
            )

            # Aggregate results
            result = aggregate_issues(issues, ai_assist=ai_assist)
            if suppressed_records:
                result["suppressed"] = suppressed_records

            if result["status"] == ScanStatus.FAILED:
                sandbox_run.status = SandboxValidationStatus.SKIPPED
                sandbox_run.summary = "Sandbox validation skipped because static scan failed."
                sandbox_run.checks = [
                    {
                        "name": "static_scan_gate",
                        "status": SandboxValidationStatus.SKIPPED.value,
                        "summary": "Runtime validation was skipped because static scan failed.",
                        "details": None,
                    }
                ]
                sandbox_run.logs = []
                sandbox_run.started_at = datetime.now(timezone.utc)
                sandbox_run.completed_at = datetime.now(timezone.utc)
            else:
                sandbox_run.status = SandboxValidationStatus.RUNNING
                sandbox_run.started_at = datetime.now(timezone.utc)
                await db.commit()

                sandbox_result = sandbox_validation_service.validate_version(
                    namespace=ns.name,
                    skill_name=skill.name,
                    slug=f"{ns.name}--{skill.name}",
                    tag=version.tag,
                    files=files,
                    access_subject=str(version.published_by or ns.owner_id),
                    network_mode=policy.sandbox_network_mode,
                    workspace_mode=policy.sandbox_workspace_mode,
                    agent_smoke_enabled=policy.sandbox_agent_smoke_enabled,
                    agent_smoke_timeout_seconds=policy.sandbox_agent_smoke_timeout_seconds,
                )
                sandbox_run.status = sandbox_result.status
                sandbox_run.engine = sandbox_result.engine
                sandbox_run.summary = sandbox_result.summary
                sandbox_run.checks = [check.to_dict() for check in sandbox_result.checks]
                sandbox_run.logs = sandbox_result.logs
                sandbox_run.completed_at = datetime.now(timezone.utc)

                if (
                    sandbox_result.status == SandboxValidationStatus.FAILED
                    and policy.require_sandbox_success
                ):
                    result["issues"].append(
                        {
                            "rule": "SANDBOX_VALIDATION_FAILED",
                            "severity": "high",
                            "message": sandbox_result.summary,
                            "file": None,
                            "snippet": None,
                            "line": None,
                        }
                    )
                    result["high_count"] += 1
                    result["status"] = ScanStatus.FAILED

            scan.status = result["status"]
            scan.issues = result["issues"]
            scan.critical_count = result["critical_count"]
            scan.high_count = result["high_count"]
            scan.medium_count = result["medium_count"]
            scan.low_count = result["low_count"]
            scan.completed_at = datetime.now(timezone.utc)

            # Promote, gate, or reject version
            if result["status"] == ScanStatus.FAILED:
                version.status = SkillVersionStatus.REJECTED
                version.gate_result = {
                    "final_status": SkillVersionStatus.REJECTED.value,
                    "issue_count": len(result["issues"]),
                    "issues": result["issues"],
                    "clinic": None,
                    "policy": {
                        "manual_review_required": policy.manual_review_required,
                        "require_examples": policy.require_examples,
                        "require_validation_spec": policy.require_validation_spec,
                        "require_sandbox_success": policy.require_sandbox_success,
                        "clinic_gate_enabled": policy.clinic_gate_enabled,
                        "min_clinic_score": policy.min_clinic_score,
                        "clinic_max_age_hours": policy.clinic_max_age_hours,
                    },
                    "technical_checks": {
                        "scan_status": scan.status.value,
                        "sandbox_status": sandbox_run.status.value if sandbox_run else None,
                        "ai_assist": ai_assist,
                    },
                }
            else:
                decision = await release_gate_service.evaluate(
                    db,
                    namespace=ns,
                    skill=skill,
                    version=version,
                    files=files,
                    policy=policy,
                    scan=scan,
                    sandbox_run=sandbox_run,
                )
                version.status = decision.status
                version.review_status = decision.review_status
                version.gate_result = _attach_ai_assist(decision.gate_result, ai_assist)
                if decision.review_status.value == "pending" and version.review_requested_at is None:
                    version.review_requested_at = datetime.now(timezone.utc)
                if decision.review_status.value == "approved":
                    version.reviewed_at = version.reviewed_at or datetime.now(timezone.utc)
                else:
                    version.reviewed_at = None
                    version.reviewed_by = None

            public_release = await get_public_release(db, version_id=version.id)
            await record_version_sync_event(
                db,
                namespace_id=ns.id,
                namespace_name=ns.name,
                skill_name=skill.name,
                description=skill.description,
                version=version,
                public_release=public_release,
                previous_state=previous_sync_state,
                reason="scan_completed" if result["status"] != ScanStatus.FAILED else "scan_failed",
            )

            from app.models.audit import AuditLog
            event_name = "scan.failed" if result["status"] == ScanStatus.FAILED else "scan.completed"
            db.add(
                AuditLog(
                    action=event_name,
                    resource_type="skill_version",
                    resource_id=version.id,
                    namespace_id=ns.id,
                    details={"skill": skill.name, "tag": version.tag, "status": result["status"].value},
                )
            )
            await dispatch_event(
                db,
                namespace_id=ns.id,
                event=event_name,
                payload={
                    "namespace": ns.name,
                    "skill": skill.name,
                    "tag": version.tag,
                    "version_id": version.id,
                    "status": result["status"].value,
                },
            )
            await db.commit()

        except Exception as exc:
            await _fail_scan(db, scan, version, str(exc))
            raise


async def _fail_scan(db, scan: ScanResult, version: SkillVersion, error_msg: str):
    sandbox_result = await db.execute(
        select(SandboxValidationRun).where(SandboxValidationRun.version_id == version.id)
    )
    sandbox_run = sandbox_result.scalar_one_or_none()
    scan.status = ScanStatus.FAILED
    scan.issues = [{"rule": "SCANNER_ERROR", "severity": "high",
                    "message": f"Scanner error: {error_msg}",
                    "file": None, "snippet": None, "line": None}]
    scan.high_count = 1
    scan.completed_at = datetime.now(timezone.utc)
    if sandbox_run is not None:
        sandbox_run.status = SandboxValidationStatus.FAILED
        sandbox_run.summary = f"Sandbox validation aborted because scan task failed: {error_msg}"
        sandbox_run.checks = [
            {
                "name": "scan_task_failure",
                "status": SandboxValidationStatus.FAILED.value,
                "summary": "Sandbox validation did not run because the scan task failed.",
                "details": {"error": error_msg},
            }
        ]
        sandbox_run.logs = [error_msg]
        sandbox_run.completed_at = datetime.now(timezone.utc)
    version.status = SkillVersionStatus.REJECTED
    await db.commit()
