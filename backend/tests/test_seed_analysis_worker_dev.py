from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.control_plane import AnalysisWorker
from app.services.analysis_service import authenticate_analysis_worker_token
from scripts.seed_analysis_worker_dev import ensure_dev_analysis_worker


DEV_TOKEN = "dkr_worker_0123abcd_dev-analysis-worker-secret"


@pytest.mark.asyncio
async def test_dev_worker_bootstrap_creates_an_authenticatable_idempotent_worker(
    async_session,
) -> None:
    worker, created = await ensure_dev_analysis_worker(
        async_session,
        token=DEV_TOKEN,
        name="DuckDock Dev Analysis Worker",
    )
    await async_session.commit()

    assert created is True
    assert worker.token_prefix == "0123abcd"
    assert worker.capabilities_json == {"bootstrap": "debug-env"}
    authenticated = await authenticate_analysis_worker_token(async_session, DEV_TOKEN)
    assert authenticated.worker.id == worker.id

    same_worker, created_again = await ensure_dev_analysis_worker(
        async_session,
        token=DEV_TOKEN,
        name="Renamed Dev Analysis Worker",
    )
    await async_session.commit()

    assert created_again is False
    assert same_worker.id == worker.id
    assert same_worker.name == "Renamed Dev Analysis Worker"
    assert len((await async_session.execute(select(AnalysisWorker))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_dev_worker_bootstrap_rejects_prefix_collision(async_session) -> None:
    await ensure_dev_analysis_worker(
        async_session,
        token=DEV_TOKEN,
        name="DuckDock Dev Analysis Worker",
    )
    await async_session.commit()

    with pytest.raises(ValueError, match="prefix already belongs to another token"):
        await ensure_dev_analysis_worker(
            async_session,
            token="dkr_worker_0123abcd_different-secret",
            name="Conflicting Worker",
        )

    with pytest.raises(HTTPException):
        await authenticate_analysis_worker_token(
            async_session,
            "dkr_worker_0123abcd_different-secret",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    ["", "dkr_worker_missing", "dkr_worker_bad-prefix_secret", "plain-secret"],
)
async def test_dev_worker_bootstrap_rejects_malformed_tokens(
    async_session,
    token: str,
) -> None:
    with pytest.raises(ValueError, match="valid analysis worker token"):
        await ensure_dev_analysis_worker(
            async_session,
            token=token,
            name="DuckDock Dev Analysis Worker",
        )
