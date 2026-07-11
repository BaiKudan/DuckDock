import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import assert_production_security, settings
from app.core.database import AsyncSessionLocal, engine
from app.core.logging import configure_logging, set_request_id
from app.api.v1.router import api_router

# SEC-02: refuse to boot a production runtime with an insecure secret/credential key.
assert_production_security(settings)

# OPS-05: emit structured JSON logs from app startup onward.
configure_logging(settings.LOG_LEVEL)

# SEC-04: explicit (non-wildcard) CORS method/header allowlists. Browsers only need the verbs
# and request headers the SPA actually sends; keep them in module scope so tests can assert the
# resolved config without issuing a live request.
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Authorization", "Content-Type", "X-Request-ID"]

# OPS-05: header used to assign / propagate a per-request correlation id.
REQUEST_ID_HEADER = "X-Request-ID"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        yield
    finally:
        from app.services.langfuse_service import langfuse_service

        client = langfuse_service.client()
        if client is not None:
            await asyncio.to_thread(client.flush)
        await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    description="Private Skills Registry + Quality Gate Platform",
    version="0.1.0",
    lifespan=lifespan,
    # SEC-02: close the interactive docs UI outside development.
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """OPS-05: assign/propagate an X-Request-ID and bind it to the log context."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = set_request_id(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            request_id_var_reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def request_id_var_reset(token) -> None:
    from app.core.logging import request_id_var

    request_id_var.reset(token)


app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


async def _check_mysql() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT 1"))


async def _check_redis() -> None:
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _check_minio() -> None:
    from app.services.artifact_service import artifact_service

    await asyncio.to_thread(artifact_service.data_client.head_bucket, Bucket=artifact_service.bucket)


async def _run_ready_check(name: str, check) -> dict[str, object]:
    try:
        await asyncio.wait_for(check(), timeout=3)
        return {"name": name, "ok": True}
    except Exception as exc:
        return {"name": name, "ok": False, "error": str(exc)}


@app.get("/readyz")
async def readyz():
    checks = await asyncio.gather(
        _run_ready_check("mysql", _check_mysql),
        _run_ready_check("redis", _check_redis),
        _run_ready_check("minio", _check_minio),
    )
    ready = all(bool(item["ok"]) for item in checks)
    payload = {"ready": ready, "checks": checks}
    if not ready:
        return JSONResponse(payload, status_code=503)
    return payload


@app.get("/.well-known/clawhub.json")
async def clawhub_well_known(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "apiBase": base,
        "minCliVersion": "0.9.0",
    }


@app.get("/.well-known/clawdhub.json")
async def clawdhub_well_known(request: Request):
    return await clawhub_well_known(request)
