"""Optional ClickHouse mirror of referral clicks (batch analytics lane).

Disabled unless ``REFERRAL_CLICKHOUSE_ENABLED`` is truthy. Postgres stays the
canonical sink; this table only serves funnel analytics.
"""

from __future__ import annotations

import os

from app.core.logging import get_logger

log = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS referral_clicks
(
    partner       LowCardinality(String),
    code          String,
    surface       LowCardinality(String),
    tier          LowCardinality(String),
    landed_at     DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (partner, code, landed_at)
"""


def mirror_click(row: dict[str, str]) -> None:
    """Insert one click into ClickHouse when the mirror is enabled."""
    if os.environ.get("REFERRAL_CLICKHOUSE_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return
    from app.domains.databus.providers.chains.lite_chain_indexer import _get_ch_client

    client = _get_ch_client()
    if client is None:
        log.warning("referral_clickhouse_unavailable")
        return
    client.execute(_DDL)
    client.execute(
        "INSERT INTO referral_clicks (partner, code, surface, tier) VALUES",
        [
            (
                row.get("partner", ""),
                row.get("code", ""),
                row.get("surface", ""),
                row.get("tier", ""),
            )
        ],
    )
