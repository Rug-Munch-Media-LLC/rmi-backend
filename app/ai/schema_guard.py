"""Schema preflight for background SQL against unprovisioned databases.

Incident 2026-09-25: the 30-minute rug-learning sweep logged
``relation "scan_results" does not exist`` as a ``warning`` forever, because
``run_rug_learning_sweep`` swallowed every exception. The Alembic migration
``0001_initial`` does create ``scan_results`` — verified by applying the chain
to a scratch database — so the fault is a *provisioning* gap, not a missing or
wrong migration. The application database is only provisioned by
``alembic upgrade head``, which nothing in the deploy path runs.

A background task that hits a missing relation is a real outage of that task,
so it must fail loudly (at ``error`` level) instead of warning every 30 minutes.
``require_relation`` is the single place that decides "this is fatal" and is
directly testable without a live database.

``UndefinedTable`` / ``UndefinedObject`` are the SQLAlchemy names for the
PostgreSQL ``42P01`` (undefined_table) and ``42704`` (undefined_object) codes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import DBAPIError, ProgrammingError

if TYPE_CHECKING:
    from app.core.logging import get_logger

    log = None

# PostgreSQL SQLSTATEs raised when a referenced relation is absent.
UNDEFINED_TABLE_SQLSTATE = "42P01"
UNDEFINED_OBJECT_SQLSTATE = "42704"


def is_missing_relation_error(exc: BaseException) -> bool:
    """True when ``exc`` is a missing-relation/database-missing fault.

    A bare ``ProgrammingError`` with no SQLSTATE is treated as missing too:
    the only ProgrammingError this codebase surfaces is an unknown relation,
    and mislabelling one as a transient fault is what hid this incident.
    """
    if isinstance(exc, DBAPIError):
        code = exc.orig.sqlstate if getattr(exc.orig, "sqlstate", None) else getattr(exc.orig, "pgcode", None)
        return code in (UNDEFINED_TABLE_SQLSTATE, UNDEFINED_OBJECT_SQLSTATE) or not code
    return isinstance(exc, ProgrammingError)


def require_relation(exc: BaseException, *, source: str, table: str) -> None:
    """Re-raise ``exc`` as a loud, self-describing provisioning error.

    Call this from a ``except`` block in a background task. Non-schema
    database faults re-raise unchanged so transient errors keep their own
    (possibly retried) handling.
    """
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    if not is_missing_relation_error(exc):
        raise exc
    msg = (
        f"{source} requires relation {table!r}, which the database does not have. "
        f"Apply migrations with `alembic upgrade head` before running this task."
    )
    logger.error("schema_preflight_failed source=%s table=%s err=%s", source, table, exc)
    raise RuntimeError(msg) from exc


__all__ = [
    "UNDEFINED_OBJECT_SQLSTATE",
    "UNDEFINED_TABLE_SQLSTATE",
    "is_missing_relation_error",
    "require_relation",
]
