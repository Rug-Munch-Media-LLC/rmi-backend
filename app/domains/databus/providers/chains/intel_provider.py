"""
DataBus provider for scraped intelligence from Pryscraper.

Reads from the scraped_intel Postgres table and returns relevant intel
for a given token/address. This is a LOCAL provider — our own data,
instant and free.

Registered as `scraped_intel` in the provider chain, used by:
  - token_security enrichment (scam reports, threat intel)
  - entity_intel resolution (entity profiles, blockchain data)
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.providers.intel_provider")


async def _scraped_intel_provider(**kwargs: Any) -> dict[str, Any] | None:
    """Query scraped_intel table for relevant intelligence.

    Parameters (from DataBus fetch):
      token_address: str — token or entity address to look up
      chain: str — blockchain chain filter
      data_type: str — optional filter (scam_report, threat_intel, etc.)
      limit: int — max results (default 10)

    Returns merged scraped intel results or None.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", ""))
    chain = kwargs.get("chain", "")
    data_type = kwargs.get("data_type", "")
    limit = min(int(kwargs.get("limit", 10)), 100)

    if not token_address:
        return None

    try:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session

        from app.models.intel import ScrapedIntel

        db_url = os.getenv("DATABASE_URL", "postgresql://localhost:5432/rmi")
        engine = create_engine(db_url)

        with Session(engine) as session:
            query = select(ScrapedIntel).where(ScrapedIntel.token_address == token_address)

            if chain:
                query = query.where(ScrapedIntel.chain == chain)
            if data_type:
                query = query.where(ScrapedIntel.data_type == data_type)

            query = query.order_by(ScrapedIntel.ingested_at.desc()).limit(limit)
            results = session.execute(query).scalars().all()

        if not results:
            return None

        # Format response
        items = []
        for r in results:
            items.append(
                {
                    "id": r.id,
                    "source": r.source,
                    "data_type": r.data_type,
                    "content": r.content,
                    "url": r.url,
                    "scraped_at": r.scraped_at.isoformat() if r.scraped_at else None,
                    "ingested_at": r.ingested_at.isoformat() if r.ingested_at else None,
                }
            )

        return {
            "token_address": token_address,
            "chain": chain or "any",
            "count": len(items),
            "results": items,
            "source": "scraped_intel (Pryscraper bridge)",
        }

    except Exception as exc:
        logger.warning("scraped_intel_provider_error", error=str(exc)[:200])
        return None
