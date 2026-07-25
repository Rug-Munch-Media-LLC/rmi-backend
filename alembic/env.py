"""Alembic migration environment."""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Ensure the app package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Read database URL from environment variable (set in .env as ALEMBIC_DATABASE_URL)
database_url = os.environ.get(
    "ALEMBIC_DATABASE_URL",
    config.get_main_option("sqlalchemy.url", "postgresql://localhost:5432/rmi"),
)
config.set_main_option("sqlalchemy.url", database_url)

from app.models import Base  # noqa: E402

# Tables defined in our ORM models — only these should be managed by Alembic.
MODEL_TABLES: set[str] = {
    "users",
    "tokens",
    "alerts",
    "alert_subscriptions",
    "labels",
    "scan_results",
    "payments",
    # Live gamification (2026-07-25)
    "live_battles",
    "battle_gifts",
    "viewer_xp_transactions",
    "challenges",
    "user_challenges",
    "achievement_defs",
    "user_achievements",
    "creator_streaks",
    "live_events",
    # Newsletter system (2026-07-26)
    "newsletters",
    "newsletter_subs",
    "newsletter_payments",
    "newsletter_deliveries",
    "newsletter_crossposts",
}

target_metadata = Base.metadata


def include_object(
    obj: object,
    name: str,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """Filter objects to only manage our ORM-model tables.

    Prevent Alembic from dropping / altering tables that belong to
    LiteLLM, community features, or other non-ORM schemas.
    """
    if type_ == "table":
        # Only manage tables we have ORM models for
        return name in MODEL_TABLES
    # Allow indexes, constraints, etc. only if their parent table passes
    return True


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
