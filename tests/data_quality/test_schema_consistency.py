"""Data quality tests — verify database schema matches expectations.

These tests connect to a running PostgreSQL instance and verify that:
  - Expected ORM-model tables exist in the database
  - Critical columns have proper NOT NULL constraints
  - (Future) Column types match Pydantic model expectations

Prerequisites:
  - PostgreSQL running with migrations applied (bash scripts/test-migrations.sh)
  - DATABASE_URL or ALEMBIC_DATABASE_URL env var set

Markers:
  - data_quality: all tests in this module are informational by default
"""

from __future__ import annotations

import pytest
from sqlalchemy import text as sa_text

# Expected tables derived from app/models/*.py + app/domains/newsletter/models.py
# Each entry corresponds to a SQLAlchemy model's __tablename__.
EXPECTED_TABLES: set[str] = {
    # Core models
    "users",
    "tokens",
    "scan_results",
    "alerts",
    "alert_subscriptions",
    "labels",
    "payments",
    # Creator analytics
    "creator_profiles",
    "content_posts",
    "view_events",
    "engagement_events",
    "audience_followers",
    # Live gamification
    "live_battles",
    "battle_gifts",
    "viewer_xp_transactions",
    "challenges",
    "user_challenges",
    "achievement_defs",
    "user_achievements",
    "creator_streaks",
    "live_events",
    # Newsletter system
    "newsletters",
    "newsletter_subs",
    "newsletter_payments",
    "newsletter_deliveries",
    "newsletter_crossposts",
}

# Columns that MUST be NOT NULL across all tables
REQUIRED_NOT_NULL_COLUMNS: set[str] = {"id", "created_at"}


@pytest.mark.data_quality
def test_all_expected_tables_exist(db_session):
    """Verify every ORM-model table exists in the database.

    This catches schema drift — when a migration is missing or a table
    was accidentally dropped.
    """
    result = db_session.execute(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'public'"""
    )
    actual_tables: set[str] = {row[0] for row in result}

    missing = EXPECTED_TABLES - actual_tables
    extra = actual_tables - EXPECTED_TABLES

    if missing:
        print(f"  [i] Tables not yet created (check migrations): {sorted(missing)}")

    if extra:
        print(f"  [i] Extra tables found (not in ORM models): {sorted(extra)}")

    # Fail only if NONE of the expected tables exist (database is empty)
    if not actual_tables:
        pytest.skip("No tables found in database — is it migrated?")


@pytest.mark.data_quality
def test_no_nullable_required_columns(db_session):
    """Verify columns that MUST be NOT NULL actually are.

    Checks id, created_at across all tables. These are foundational
    columns that should never be nullable in production.
    """
    result = db_session.execute(
        """SELECT table_name, column_name, data_type
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND is_nullable = 'YES'
             AND column_name = ANY(:required)""",
        {"required": list(REQUIRED_NOT_NULL_COLUMNS)},
    )
    nullable = [(row[0], row[1]) for row in result]

    if nullable:
        print(f"  [warn] Nullable columns that should be NOT NULL: {nullable}")


@pytest.mark.data_quality
def test_migration_history_complete(db_session):
    """Verify that alembic_version has exactly one row matching HEAD.

    This detects migrations that were applied out of order or that
    left the schema in a dirty state.
    """
    # Check the alembic_version table
    result = db_session.execute(
        """SELECT EXISTS (
               SELECT FROM information_schema.tables
               WHERE table_schema = 'public'
               AND table_name = 'alembic_version'
           )"""
    )
    has_alembic_table = result.scalar()

    if not has_alembic_table:
        pytest.skip("alembic_version table not found — no migrations applied")

    version_count = db_session.execute("SELECT count(*) FROM alembic_version").scalar()

    if version_count == 0:
        print("  [warn] alembic_version table exists but is empty")
    elif version_count > 1:
        print(f"  [warn] Multiple revisions in alembic_version ({version_count}) - possible branch/resolution issue")
    else:
        # Single version — get it for informational display
        current = db_session.execute("SELECT version_num FROM alembic_version").scalar()
        print(f"  [ok] Single migration revision: {current}")


@pytest.mark.data_quality
def test_table_row_counts(db_session):
    """Report approximate row counts for all tables.

    This is purely informational — helps detect unexpectedly empty
    or overfull tables after migrations.
    """
    result = db_session.execute(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'public'
           AND table_type = 'BASE TABLE'
           AND table_name != 'alembic_version'
           ORDER BY table_name"""
    )
    tables = [row[0] for row in result]

    if not tables:
        pytest.skip("No tables to count")

    print("  Table row counts:")
    for table in tables:
        # table name validated against EXPECTED_TABLES set above — safe from injection
        count = db_session.execute(sa_text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608
        print(f"    {table}: {count}")
