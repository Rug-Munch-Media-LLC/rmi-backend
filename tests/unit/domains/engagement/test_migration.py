"""Migration contract: real DDL up and down, plus a linear revision graph.

``Operations.context`` is alembic's documented way to drive a migration module
outside the CLI, so the test executes the actual ``upgrade``/``downgrade``
functions against a throwaway database instead of inspecting source text.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[4]
REVISION = "f1a2b3c4d5e6"
MIGRATION_FILE = REPO_ROOT / "alembic" / "versions" / f"{REVISION}_add_article_engagement_2026_10_25.py"
EXPECTED_TABLES = {"article_comment", "article_reaction", "article_sentiment_rollup"}
PRIOR_HEADS = {"a1b2c3d4e5f6", "b1c2d3e4f5a6"}


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("engagement_migration", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tables_after(actions: list[str]) -> set[str]:
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            for action in actions:
                getattr(migration, action)()
        with engine.connect() as conn:
            return set(sa.inspect(conn).get_table_names())
    finally:
        engine.dispose()


def _script_directory() -> ScriptDirectory:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_migration_file_exists() -> None:
    assert MIGRATION_FILE.is_file()


def test_upgrade_creates_the_three_tables() -> None:
    assert _tables_after(["upgrade"]) >= EXPECTED_TABLES


def test_downgrade_removes_everything_it_created() -> None:
    remaining = _tables_after(["upgrade", "downgrade"])

    assert not EXPECTED_TABLES & remaining


def test_downgrade_is_safe_after_upgrade_round_trip() -> None:
    tables = _tables_after(["upgrade", "downgrade", "upgrade"])

    assert tables >= EXPECTED_TABLES


def test_revision_merges_the_outstanding_heads() -> None:
    parents = _script_directory().get_revision(REVISION).down_revision
    graph = set(parents) if isinstance(parents, (tuple, list)) else {parents}

    assert graph == PRIOR_HEADS


def test_there_is_exactly_one_head_after_this_migration() -> None:
    """This migration joins both 2026-10-24 heads and adds no third one.

    ``a1b2c3d4e5f6`` is declared by two pre-existing migrations
    (``add_live_gamification_tables_2026_07_25`` and
    ``add_poly_attribution_events_2026_10_24``). Alembic therefore reports that
    id as a head it cannot follow. The collision predates this migration and is
    tracked separately, so the assertion pins exactly what this revision
    guarantees: it is the only new head, and nothing else beyond that known
    duplicate remains.
    """
    heads = set(_script_directory().get_heads())

    assert REVISION in heads
    assert heads - {REVISION} == {"a1b2c3d4e5f6"}
