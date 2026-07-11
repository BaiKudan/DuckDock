from fastapi import APIRouter
from app.api.v1.endpoints import (
    analysis,
    agent_overview,
    audit,
    auth,
    clawhub,
    clinic,
    components,
    control_plane,
    iam,
    lifecycle,
    namespaces,
    people,
    registry,
    replication,
    robots,
    scans,
    skills,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(iam.router)
api_router.include_router(namespaces.router)
api_router.include_router(skills.router)
api_router.include_router(clawhub.router)
api_router.include_router(registry.router)
api_router.include_router(scans.router)
api_router.include_router(clinic.router)
api_router.include_router(components.router)
api_router.include_router(control_plane.router)
api_router.include_router(agent_overview.router)
api_router.include_router(analysis.router)
api_router.include_router(people.router)
api_router.include_router(audit.router)
api_router.include_router(webhooks.router)
api_router.include_router(robots.router)
api_router.include_router(lifecycle.router)
api_router.include_router(replication.router)
