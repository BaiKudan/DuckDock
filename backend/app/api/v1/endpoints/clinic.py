from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.deps import (
    DB,
    CurrentUser,
    require_namespace_clinic_reader,
    require_namespace_clinic_runner,
)
from app.models.clinic import ClinicEvaluation, EvalStatus
from app.models.namespace import Namespace
from app.schemas.clinic import EvaluationOut, EvaluationSummary, DIMENSION_META
from app.services.audit_service import audit
from app.services.langfuse_service import langfuse_service
from app.workers.clinic_tasks import run_clinic_evaluation

router = APIRouter(prefix="/clinic", tags=["clinic"])


async def _check_ns_access(ns_name: str, user_id: int, db: DB) -> Namespace:
    ns_q = await db.execute(
        select(Namespace).where(Namespace.name == ns_name, Namespace.deleted_at.is_(None))
    )
    ns = ns_q.scalar_one_or_none()
    if not ns:
        raise HTTPException(404, "Namespace not found")
    return ns


@router.post("/namespaces/{ns_name}/evaluate", response_model=EvaluationSummary, status_code=202)
async def trigger_evaluation(ns_name: str, db: DB, current_user: CurrentUser):
    """Trigger a new Clinic evaluation for a namespace."""
    ns = await _check_ns_access(ns_name, current_user.id, db)
    await require_namespace_clinic_runner(current_user, ns.id, db)

    evaluation = ClinicEvaluation(
        namespace_id=ns.id,
        status=EvalStatus.PENDING,
        triggered_by=current_user.id,
    )
    db.add(evaluation)
    await db.flush()
    await db.commit()
    await db.refresh(evaluation)

    run_clinic_evaluation.delay(evaluation.id)
    await audit(
        db,
        user=current_user,
        action="clinic.triggered",
        resource_type="clinic_evaluation",
        resource_id=evaluation.id,
        namespace_id=ns.id,
        details={"namespace": ns.name},
    )
    return evaluation


@router.get("/namespaces/{ns_name}/evaluations", response_model=list[EvaluationSummary])
async def list_evaluations(ns_name: str, db: DB, current_user: CurrentUser):
    ns = await _check_ns_access(ns_name, current_user.id, db)
    await require_namespace_clinic_reader(current_user, ns.id, db)
    q = await db.execute(
        select(ClinicEvaluation)
        .where(ClinicEvaluation.namespace_id == ns.id)
        .order_by(ClinicEvaluation.created_at.desc())
        .limit(20)
    )
    return q.scalars().all()


@router.get("/evaluations/{eval_id}", response_model=EvaluationOut)
async def get_evaluation(eval_id: int, db: DB, current_user: CurrentUser):
    q = await db.execute(
        select(ClinicEvaluation).where(ClinicEvaluation.id == eval_id)
    )
    evaluation = q.scalar_one_or_none()
    if not evaluation:
        raise HTTPException(404, "Evaluation not found")

    # Permission check via namespace membership
    await _check_ns_access_by_id(evaluation.namespace_id, current_user, db)
    payload = EvaluationOut.model_validate(evaluation).model_dump()
    if evaluation.trace_id:
        payload["langfuse_trace_id"] = evaluation.trace_id
        client = langfuse_service.client()
        payload["langfuse_trace_url"] = (
            client.get_trace_url(trace_id=evaluation.trace_id) if client is not None else None
        )
    else:
        trace = langfuse_service.get_trace_link_for_evaluation(
            evaluation_id=evaluation.id,
            created_at=evaluation.created_at,
        )
        if trace:
            payload["langfuse_trace_id"] = trace["trace_id"]
            payload["langfuse_trace_url"] = trace["trace_url"]
    return payload


@router.get("/dimensions")
async def list_dimensions():
    """Return metadata for all 8 evaluation dimensions."""
    return DIMENSION_META


async def _check_ns_access_by_id(ns_id: int, current_user: CurrentUser, db: DB):
    ns_q = await db.execute(
        select(Namespace).where(Namespace.id == ns_id, Namespace.deleted_at.is_(None))
    )
    if ns_q.scalar_one_or_none() is None:
        raise HTTPException(404, "Namespace not found")
    await require_namespace_clinic_reader(current_user, ns_id, db)
