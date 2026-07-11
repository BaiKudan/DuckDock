from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings


@asynccontextmanager
async def worker_db_session(*, echo: bool = False) -> AsyncIterator[Any]:
    engine = create_async_engine(settings.DATABASE_URL, echo=echo)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            yield db
    finally:
        await engine.dispose()
