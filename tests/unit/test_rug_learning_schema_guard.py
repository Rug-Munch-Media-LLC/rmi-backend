"""Regression tests for the rug-learning sweep schema preflight.

These fail on the pre-fix behaviour, where ``run_rug_learning_sweep`` caught
every exception and logged ``rug_learning_sweep_query_failed`` at ``warning``
level. Against the unprovisioned application database that produced the same
line every 30 minutes, with no alert and no signal to act on.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import ProgrammingError

from app.ai.rug_learning_sweep import run_rug_learning_sweep
from app.ai.schema_guard import is_missing_relation_error, require_relation


@pytest.fixture
def recorded_logs():
    """Capture the app's structlog output (caplog does not see it).

    ``capture_logs`` returns the event dict only; the level lives on the logger
    call, so a processor injects it. Without this the level is always ``None`` and
    any "must log at ERROR" assertion passes or fails for the wrong reason.
    """
    import structlog  # noqa: F401
    from structlog.testing import capture_logs

    def _with_level(_logger, method_name, event_dict):
        event_dict["level"] = method_name
        return event_dict

    with capture_logs(processors=[_with_level]) as logs:

        def _events() -> list[str]:
            return [f"{r.get('level')} {r.get('event')}" for r in logs if r.get("event")]

        yield _events


def _missing_table_error() -> ProgrammingError:
    """Build the exact error the live database raised (SQLSTATE 42P01)."""
    orig = AsyncAdapt_asyncpg_dbapi.Error(
        'relation "scan_results" does not exist',
    )
    orig.errno = 42
    orig.sqlstate = "42P01"
    return ProgrammingError("SELECT ... FROM scan_results", {}, orig)


class TestIsMissingRelationError:
    def test_missing_relation_is_detected(self):
        assert is_missing_relation_error(_missing_table_error())

    def test_unrelated_value_error_is_not(self):
        assert not is_missing_relation_error(ValueError("nope"))


class TestRequireRelation:
    def test_missing_relation_raises_actionable_runtime_error(self):
        with pytest.raises(RuntimeError, match="alembic upgrade head"):
            require_relation(
                _missing_table_error(),
                source="run_rug_learning_sweep",
                table="scan_results",
            )

    def test_transient_error_is_reraised_unchanged(self):
        """Non-schema faults keep their own handling — we must not mask them."""
        original = OSError("connection reset by peer")
        with pytest.raises(OSError, match="connection reset by peer") as excinfo:
            require_relation(original, source="run_rug_learning_sweep", table="scan_results")
        assert excinfo.value is original


class TestSweepFailsLoudly:
    """The sweep must not swallow a missing relation."""

    async def test_sweep_raises_instead_of_warning(self, monkeypatch, recorded_logs):
        """Pre-fix: sweep returned zeroed stats and logged a warning. Now: raises."""
        import app.ai.rug_learning_sweep as sweep_mod

        class _FailingSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, _stmt):
                raise _missing_table_error()

        monkeypatch.setattr("app.core.database.async_session_factory", lambda: _FailingSession())

        with pytest.raises(RuntimeError, match="alembic upgrade head"):
            await run_rug_learning_sweep()

        events = recorded_logs()
        assert not any("rug_learning_sweep_query_failed" in e for e in events), (
            "sweep must not silently warn about a missing table"
        )
        assert any("schema_preflight_failed" in e for e in events), (
            f"missing relation must be logged loudly; got {events}"
        )
        assert any("error" in e.lower() for e in events), f"must log at error level, not warning; got {events}"
        assert sweep_mod.DEDUP_TTL_SECONDS > 0
