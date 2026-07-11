from fastapi import APIRouter, status

from app.core.deps import DB, RuntimeManagerUser, RuntimeReaderUser
from app.schemas.agent_overview import AgentInsightCreateOut, AgentInsightJobOut, AgentOverviewOut
from app.services.agent_overview_service import (
    build_agent_overview,
    create_agent_insight_job,
    get_agent_insight_job,
)
from app.services.audit_service import audit
from app.workers.agent_insight_tasks import run_agent_insight_job_task


router = APIRouter(prefix="/agent-overview", tags=["agent-overview"])


@router.get("/users/{user_id}/runtimes/{runtime_id}", response_model=AgentOverviewOut)
async def get_agent_overview(
    user_id: int,
    runtime_id: int,
    db: DB,
    current_user: RuntimeReaderUser,
):
    return await build_agent_overview(db, user_id=user_id, runtime_id=runtime_id)


@router.post(
    "/users/{user_id}/runtimes/{runtime_id}/analyze",
    response_model=AgentInsightCreateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_overview_analysis(
    user_id: int,
    runtime_id: int,
    db: DB,
    current_user: RuntimeManagerUser,
):
    job = await create_agent_insight_job(
        db,
        user_id=user_id,
        runtime_id=runtime_id,
        requested_by=current_user.id,
    )
    await audit(
        db,
        user=current_user,
        action="agent_overview.analysis_requested",
        resource_type="agent_insight_job",
        resource_id=job.id,
        details={"user_id": user_id, "runtime_id": runtime_id},
    )
    run_agent_insight_job_task.delay(job.id)
    return AgentInsightCreateOut(job=AgentInsightJobOut.model_validate(job), queued=True)


@router.get("/insights/{job_id}", response_model=AgentInsightJobOut)
async def get_agent_overview_analysis(
    job_id: int,
    db: DB,
    current_user: RuntimeReaderUser,
):
    return AgentInsightJobOut.model_validate(await get_agent_insight_job(db, job_id=job_id))
