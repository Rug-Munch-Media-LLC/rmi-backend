"""Persistence — Postgres save, Redis cache, RAG publishing, deployer labeling."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)


def _to_persist_dict(result: ScanResult) -> dict[str, Any]:
    """Convert ScanResult to a plain dict suitable for JSON storage."""
    return {
        "token_address": result.token_address,
        "chain": result.chain,
        "scanned_at": result.scanned_at,
        "name": result.name,
        "symbol": result.symbol,
        "price_usd": result.price_usd,
        "market_cap_usd": result.market_cap_usd,
        "liquidity_usd": result.liquidity_usd,
        "volume_24h_usd": result.volume_24h_usd,
        "age_hours": result.age_hours,
        "safety_score": result.safety_score,
        "confidence": result.confidence,
        "risk_level": result.risk_level,
        "risk_flags": result.risk_flags,
        "green_flags": result.green_flags,
        "tier": result.tier,
        "chain_indexer_patterns": result.chain_indexer_patterns,
        "scan_duration_ms": result.scan_duration_ms,
        "modules_run": result.modules_run,
        "enrichment_sources": result.enrichment_sources,
    }


async def persist_result(result: ScanResult) -> None:
    """Persist scan result to Postgres + Redis (fire-and-forget).

    1. Save ScanResult ORM row to ``scan_results`` table
    2. Best-effort upsert token into ``tokens`` table if new
    3. Cache in Redis for 1 hour
    4. All errors are logged but never raised
    """
    # ── 1. Save scan_result row ──
    try:
        from app.models.scan import ScanResult as ScanResultORM

        session = await _get_async_session()
        if session is None:
            return
        async with session:
            raw_json = json.dumps(_to_persist_dict(result), default=str)

            scan_orm = ScanResultORM(
                id=uuid.uuid4(),
                token_address=result.token_address,
                chain=result.chain,
                symbol=result.symbol[:64] if result.symbol else "",
                name=result.name[:256] if result.name else "",
                safety_score=int(round(result.safety_score)),
                risk_score=int(round(100 - result.safety_score)),
                risk_level=result.risk_level,
                risk_flags=result.risk_flags[:50],
                warnings=result.green_flags[:50],
                modules_run=result.modules_run[:50],
                extra={"raw": raw_json},
                scanned_at=datetime.fromisoformat(result.scanned_at) if result.scanned_at else datetime.now(UTC),
                tier_required=result.tier,
                confidence=int(round(result.confidence)),
            )
            session.add(scan_orm)

            # ── 2. Best-effort token upsert ──
            await _maybe_upsert_token(session, result)

            await session.commit()
            logger.info(
                "scan_persisted",
                chain=result.chain,
                address=result.token_address,
                scan_id=str(scan_orm.id),
            )

    except Exception:
        logger.exception(
            "scan_persist_failed",
            chain=result.chain,
            address=result.token_address,
        )

    # ── 3. Redis cache (independent of Postgres) ──
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is not None:
            cache_key = f"sentinelscan:{result.chain}:{result.token_address}"
            cache_val = json.dumps(_to_persist_dict(result), default=str)
            await r.setex(cache_key, 3600, cache_val)
            logger.debug("scan_redis_cached", key=cache_key)
    except Exception:
        logger.exception("scan_redis_cache_failed")


async def _maybe_upsert_token(session: Any, result: ScanResult) -> None:
    """Best-effort upsert of discovered token into the ``tokens`` table."""
    if not result.symbol:
        return
    try:
        from sqlalchemy import select

        from app.models.token import Token

        stmt = select(Token).where(
            Token.chain == result.chain,
            Token.address == result.token_address,
        )
        tok_result = await session.execute(stmt)
        existing_token = tok_result.scalar_one_or_none()

        if existing_token is None:
            token_orm = Token(
                address=result.token_address,
                chain=result.chain,
                name=result.name[:256] if result.name else "",
                symbol=result.symbol[:64] if result.symbol else "",
                decimals=9,
                total_supply=0.0,
                circulating_supply=0.0,
                holders=0,
                verified=False,
                risk_score=int(round(100 - result.safety_score)),
                risk_level=result.risk_level,
                extra={"first_scanned_at": result.scanned_at},
            )
            session.add(token_orm)
            logger.info(
                "token_created_from_scan",
                chain=result.chain,
                address=result.token_address,
                symbol=result.symbol,
            )
    except Exception:
        logger.info(
            "token_upsert_skipped",
            chain=result.chain,
            address=result.token_address,
            detail="Token ORM model may not match DB schema; upsert skipped",
        )


async def _get_async_session() -> Any | None:
    """Get an async SQLAlchemy session, falling back to direct connection if needed."""
    from app.core.database import async_session_factory, engine

    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return async_session_factory()
    except (ConnectionRefusedError, OSError, ConnectionError):
        pass
    except Exception:
        pass

    import os

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST") or "rmi-postgres"
    user = os.getenv("POSTGRES_USER", "rmi")
    password = os.getenv("POSTGRES_PASSWORD", "rmi")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "rmi")
    fallback_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"

    try:
        fallback_engine = create_async_engine(fallback_url, pool_size=2, max_overflow=2)
        fallback_factory = async_sessionmaker(
            fallback_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("scan_persist_fallback_engine", host=host, db=db)
        return fallback_factory()
    except Exception as exc:
        logger.warning("scan_persist_fallback_failed", host=host, error=str(exc)[:100])
        return None


async def publish_rag_finding(result: ScanResult) -> None:
    """Publish high-risk scan finding to rmi-rag via Redis channel ``rmi:rag:ingest``."""
    from app.core.redis import get_redis_async

    r = get_redis_async()
    if r is None:
        logger.warning("Redis unavailable — skipping RAG finding publish")
        return

    payload = {
        "token_address": result.token_address,
        "chain": result.chain,
        "name": result.name,
        "symbol": result.symbol,
        "safety_score": result.safety_score,
        "confidence": result.confidence,
        "risk_level": result.risk_level,
        "risk_flags": result.risk_flags[:20],
        "green_flags": result.green_flags[:10],
        "price_usd": result.price_usd,
        "liquidity_usd": result.liquidity_usd,
        "age_hours": result.age_hours,
        "scanned_at": result.scanned_at,
        "modules_run": result.modules_run[:30],
    }

    payload_json = json.dumps(payload)

    await r.publish("rmi:rag:ingest", payload_json)
    await r.lpush("rmi:rag:findings_queue", payload_json)

    logger.info(
        "rag_finding_published",
        token=result.token_address,
        chain=result.chain,
        risk=result.risk_level,
    )


async def auto_label_deployer(result: ScanResult) -> None:
    """Auto-label the deployer wallet when dev_reputation shows high risk."""
    dev = result.dev_reputation
    if not dev or not isinstance(dev, dict):
        return

    deployer_addr = dev.get("deployer_address") or dev.get("address", "")
    risk_score = float(dev.get("risk_score", 0) or 0)
    risk_label = str(dev.get("risk_label", dev.get("reputation_label", "UNKNOWN")))

    if risk_score < 70 or not deployer_addr or deployer_addr == "unknown":
        return

    from app.domains.scanner.shared.deployer_labeler import auto_label_deployer as _auto_label

    await _auto_label(
        deployer_address=deployer_addr,
        chain=result.chain,
        risk_score=risk_score,
        risk_label=risk_label,
        token_address=result.token_address,
        token_name=result.name,
        token_symbol=result.symbol,
        safety_score=result.safety_score,
        risk_flags=result.risk_flags,
        source="unified_token_scanner",
    )
