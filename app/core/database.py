"""Async SQLAlchemy 2.0 engine and session factory.

Provides the `get_db` FastAPI dependency for request-scoped sessions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# Ensure the async driver prefix is present for create_async_engine
_db_url = settings.database_url
if _db_url and "+" not in _db_url.split("://")[0] if "://" in _db_url else False:
    # Add +asyncpg driver suffix for create_async_engine
    scheme, rest = _db_url.split("://", 1)
    _db_url = f"{scheme}+asyncpg://{rest}"

engine = create_async_engine(
    _db_url,
    pool_size=15,
    max_overflow=30,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped async session.

    Commits on success, rolls back on exception, always closes.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except (ConnectionError, OSError, TimeoutError):
            await session.rollback()
            raise
        finally:
            await session.close()
