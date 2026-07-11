"""
Clinic 评测 Celery 任务

流程：
  1. 加载 namespace 下所有 skills + 最新版本文件内容 + scan results
  2. 调用 ClinicService.evaluate() 得到 8 维结果
  3. 写入 ClinicEvaluation
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.clinic import ClinicEvaluation, EvalStatus
from app.models.namespace import Namespace
from app.models.skill import Skill, SkillVersionStatus
from app.models.scan import ScanResult
from app.services.clinic_service import clinic_service
from app.services.git_service import git_service, GitServiceError
from app.services.webhook_service import dispatch_event
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session


@celery_app.task(name="run_clinic_evaluation", bind=True, max_retries=1)
def run_clinic_evaluation(self, evaluation_id: int):
    try:
        asyncio.run(_async_evaluate(evaluation_id))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=15)


async def _async_evaluate(evaluation_id: int):
    async with worker_db_session() as db:
        eval_result = await db.execute(
            select(ClinicEvaluation).where(ClinicEvaluation.id == evaluation_id)
        )
        evaluation = eval_result.scalar_one_or_none()
        if not evaluation:
            return

        # Mark running
        evaluation.status = EvalStatus.RUNNING
        evaluation.started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            ns_result = await db.execute(
                select(Namespace).where(
                    Namespace.id == evaluation.namespace_id,
                    Namespace.deleted_at.is_(None),
                )
            )
            ns = ns_result.scalar_one()

            # Load all skills with latest version
            skills_q = await db.execute(
                select(Skill)
                .where(Skill.namespace_id == ns.id, Skill.deleted_at.is_(None))
                .options(selectinload(Skill.versions))
            )
            skills = skills_q.scalars().all()

            # Load previous evaluation for trend comparison
            prev_q = await db.execute(
                select(ClinicEvaluation)
                .where(
                    ClinicEvaluation.namespace_id == ns.id,
                    ClinicEvaluation.id != evaluation_id,
                    ClinicEvaluation.status == EvalStatus.COMPLETED,
                )
                .order_by(ClinicEvaluation.created_at.desc())
                .limit(1)
            )
            prev_eval = prev_q.scalar_one_or_none()
            prev_scores = prev_eval.dimension_scores if prev_eval else None

            # Build skills_data list
            skills_data = []
            scan_results_map = {}

            for skill in skills:
                prod_versions = [
                    v for v in skill.versions
                    if v.status == SkillVersionStatus.PRODUCTION
                ]
                latest_prod = sorted(
                    prod_versions, key=lambda v: v.created_at, reverse=True
                )
                latest = latest_prod[0] if latest_prod else (
                    sorted(skill.versions, key=lambda v: v.created_at, reverse=True)[0]
                    if skill.versions else None
                )

                skill_md = ""
                system_prompt = ""
                if latest:
                    try:
                        files = git_service.get_version_files(ns.name, skill.name, latest.tag)
                        skill_md = files.get("SKILL.md", "")
                        system_prompt = files.get("system_prompt.md", "")
                    except GitServiceError:
                        pass

                    # Load scan result for this version
                    scan_q = await db.execute(
                        select(ScanResult).where(ScanResult.version_id == latest.id)
                    )
                    scan = scan_q.scalar_one_or_none()
                    if scan:
                        scan_results_map[skill.name] = {
                            "status": scan.status,
                            "critical_count": scan.critical_count,
                            "high_count": scan.high_count,
                            "medium_count": scan.medium_count,
                            "low_count": scan.low_count,
                        }

                skills_data.append({
                    "name": skill.name,
                    "description": skill.description,
                    "has_production": bool(latest_prod),
                    "version_count": len(skill.versions),
                    "last_updated": latest.created_at.isoformat() if latest else None,
                    "latest_metadata": latest.skill_metadata if latest else None,
                    "skill_md": skill_md,
                    "system_prompt": system_prompt,
                })

            # Run evaluation
            result = clinic_service.evaluate(
                skills_data=skills_data,
                scan_results=scan_results_map,
                prev_scores=prev_scores,
                evaluation_id=evaluation.id,
                namespace_id=ns.id,
                namespace_name=ns.name,
            )

            evaluation.status = EvalStatus.COMPLETED
            evaluation.overall_score = result["overall_score"]
            evaluation.grade = result["grade"]
            evaluation.dimension_scores = result["dimension_scores"]
            evaluation.recommendations = result["recommendations"]
            evaluation.ai_assist = result.get("ai_assist")
            evaluation.trace_id = result.get("langfuse_trace_id")
            evaluation.completed_at = datetime.now(timezone.utc)
            from app.models.audit import AuditLog
            db.add(
                AuditLog(
                    action="clinic.completed",
                    resource_type="clinic_evaluation",
                    resource_id=evaluation.id,
                    namespace_id=ns.id,
                    details={"grade": evaluation.grade, "overall_score": evaluation.overall_score},
                )
            )
            await dispatch_event(
                db,
                namespace_id=ns.id,
                event="clinic.completed",
                payload={
                    "namespace": ns.name,
                    "evaluation_id": evaluation.id,
                    "grade": evaluation.grade,
                    "overall_score": evaluation.overall_score,
                },
            )
            await db.commit()

        except Exception as exc:
            evaluation.status = EvalStatus.FAILED
            evaluation.error_message = str(exc)[:512]
            evaluation.completed_at = datetime.now(timezone.utc)
            await db.commit()
            raise
