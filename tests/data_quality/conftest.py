"""Fixtures for data quality tests.

Provides a db_session fixture that connects to a PostgreSQL instance.
Set DATABASE_URL or ALEMBIC_DATABASE_URL to point at the test database.
Falls back to the local development default if neither is set.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy import Engine
    from sqlalchemy.orm import Session


@pytest.fixture(scope="session")
def db_engine() -> Generator[Engine, None, None]:
    """Create a SQLAlchemy engine for the test database.

    Uses ALEMBIC_DATABASE_URL first (for alignment with migration tests),
    then DATABASE_URL, then a local default.
    """
    db_url = (
        os.environ.get("ALEMBIC_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://postgres:postgres@localhost:5432/postgres"
    )
    engine = create_engine(db_url, pool_pre_ping=True)
    # Verify connection is alive
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Generator[Session, None, None]:
    """Provide a transactional database session per test function."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session_local = sessionmaker(bind=connection)
    session = session_local()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
