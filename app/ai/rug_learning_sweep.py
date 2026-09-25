"""Scheduled sweep: learn from confirmed rugs scanned in the last 7 days.

Split out of ``app.ai.rug_learning`` (shrink-only size ratchet) — the sweep is
the one part of that module that talks to Postgres.

The sweep used to swallow every query error and log
``rug_learning_sweep_query_failed`` at ``warning`` level. Against the
unprovisioned application database that produced one such line every 30
minutes, indefinitely, with nothing paging. A missing relation is a real
outage of this task, so it now fails loudly via
:func:`app.ai.schema_guard.require_relation`.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

from app.ai.rug_learning import check_rug_confirmed, record_confirmed_rug
from app.ai.schema_guard import require_relation
from app.core.logging import get_logger

log = get_logger(__name__)

DEDUP_TTL_SECONDS = 30 * 86400  # 30 days


async def run_rug_learning_sweep(max_tokens: int = 100) -> dict[str, int]:
    """Sweep recently scanned tokens; embed newly confirmed rugs.

    Called by the lifespan background loop every 30 minutes. Deduplicates
    via a Redis marker so each token is learned at most once.

    Raises:
        RuntimeError: ``scan_results`` is absent, meaning the database was
            never provisioned with ``alembic upgrade head``.
    """
    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.models.scan import ScanResult as ScanResultORM

    stats = {"checked": 0, "confirmed": 0, "learned": 0, "skipped": 0}
    try:
        cutoff = datetime.now(UTC) - timedelta(days=7)
        async with async_session_factory() as session:
            stmt = (
                select(ScanResultORM.chain, ScanResultORM.token_address)
                .where(ScanResultORM.scanned_at >= cutoff)
                .group_by(ScanResultORM.chain, ScanResultORM.token_address)
                .order_by(func.max(ScanResultORM.scanned_at).desc())
                .limit(max_tokens)
            )
            rows = (await session.execute(stmt)).all()
    except Exception as e:
        require_relation(e, source="run_rug_learning_sweep", table=ScanResultORM.__tablename__)
        log.warning("rug_learning_sweep_query_failed err=%s", e)
        return stats

    from app.domains.scanner.engines._exceptions import get_redis_async

    r = get_redis_async()
    for chain, address in rows:
        stats["checked"] += 1
        dedup_key = f"rug_learned:{chain}:{address.lower()}"
        try:
            if r is not None and await r.exists(dedup_key):
                stats["skipped"] += 1
                continue
        except Exception:
            r = None

        confirmed = await check_rug_confirmed(chain, address)
        if not confirmed:
            continue
        stats["confirmed"] += 1
        ok = await record_confirmed_rug(
            scan_result=confirmed["scan_result"],
            chain=chain,
            address=address,
            rug_type=confirmed["rug_type"],
        )
        if ok:
            stats["learned"] += 1
            if r is not None:
                with contextlib.suppress(Exception):
                    await r.setex(dedup_key, DEDUP_TTL_SECONDS, "1")
    log.info("rug_learning_sweep_complete stats=%s", stats)
    return stats
