"""pg_stat_statements monitoring endpoint (slow query tracking)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from . import verify_admin

pg_stats_router = APIRouter()

_PG_STATS_SQL = """
SELECT query, calls, total_exec_time, mean_exec_time, rows
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT :limit
"""


@pg_stats_router.get("/pg-stats")
async def admin_pg_stats(
    request: Request,
    limit: int = 20,
    _=Depends(verify_admin),
) -> dict[str, Any]:
    """Return top queries by total execution time from pg_stat_statements."""
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        rows = (await session.execute(text(_PG_STATS_SQL), {"limit": limit})).mappings().all()
        return {"queries": [dict(r) for r in rows]}
