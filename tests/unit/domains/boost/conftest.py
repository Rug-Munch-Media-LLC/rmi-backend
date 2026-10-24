"""Fixtures for the paid boost unit tests.

Real SQLAlchemy metadata on an in-memory SQLite database — not a mock. Only the
boost tables are created: the rest of the model package uses Postgres-only
column types that SQLite cannot compile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.boost import BoostEvent, BoostPurchase
from tests.unit.domains.boost.helpers import RecordingVerifier

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [BoostPurchase.__table__, BoostEvent.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def verifier() -> RecordingVerifier:
    return RecordingVerifier()


@pytest.fixture
def denying_verifier() -> RecordingVerifier:
    return RecordingVerifier(deny=True)
