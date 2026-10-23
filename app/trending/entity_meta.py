"""Known-entity veto from ``wallet_memory.entity_labels``.

A token whose address is labelled as an exchange / bridge / mixer / scam
address is not a tradable "trending token" — it is infrastructure. Those rows
get a near-zero score instead of being deleted.
"""

from __future__ import annotations

from typing import Any

from app.trending.ch_client import query_rows

ENTITY_SQL = """
SELECT chain_id, address, any(entity_type) AS entity_type, any(name) AS name
FROM wallet_memory.entity_labels
WHERE address IN %(addresses)s
GROUP BY chain_id, address
"""

BLOCKING_TYPES = frozenset({"exchange", "bridge", "mixer", "malicious", "mev_bot", "dex"})


def fetch_entities(addresses: list[str]) -> dict[str, dict[str, Any]]:
    """Map address -> entity label record. Returns {} when nothing matches."""
    if not addresses:
        return {}
    rows = query_rows(ENTITY_SQL, {"addresses": tuple(addresses)})
    keys = ("chain_id", "address", "entity_type", "name")
    return {rec["address"]: rec for rec in (dict(zip(keys, r, strict=True)) for r in rows)}


def entity_penalty(record: dict[str, Any] | None) -> float:
    """Score multiplier for a known entity address."""
    if not record:
        return 1.0
    return 0.1 if record.get("entity_type") in BLOCKING_TYPES else 0.6
