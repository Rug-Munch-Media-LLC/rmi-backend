"""Kùzu graph store — wallet/deployer forensics graph (ADR-008).

Embedded, columnar graph engine (Cypher) built from ClickHouse
``wallet_memory``. Derived + rebuildable — never a source of truth.

Ownership: rmi-backend is the single writer of the Kùzu file; other services
query through the backend API, never by opening the file concurrently.

Rebuild:  python -c "from app.graph_store import build_graph; build_graph()"
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import kuzu

GRAPH_PATH = os.getenv("KUZU_GRAPH_PATH", "/var/lib/rmi/graph/wallets.kuzu")
CH_URL = os.getenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
CH_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")


def _db() -> kuzu.Database:
    Path(GRAPH_PATH).parent.mkdir(parents=True, exist_ok=True)
    return kuzu.Database(GRAPH_PATH)


def _query(conn: kuzu.Connection, cypher: str, params: dict[str, Any] | None = None) -> kuzu.QueryResult:
    """Execute one Cypher statement and return its single result set.

    kuzu's stubs type ``execute()`` as ``QueryResult | list[QueryResult]``
    (multi-statement strings return a list). Every call here is a single
    statement, so normalise at the boundary.
    """
    result = conn.execute(cypher, params) if params is not None else conn.execute(cypher)
    if isinstance(result, list):
        result = result[0]
    return result


def _row(result: kuzu.QueryResult) -> list[Any]:
    """Fetch the next row as a list (kuzu stubs type ``get_next`` as dict)."""
    row = result.get_next()
    return list(row) if isinstance(row, (list, tuple)) else list(row.values())


def build_graph() -> int:
    """(Re)build the graph from ClickHouse. Returns node count.

    Schema:
      Wallet(address PK, label, tier)
      Deployer(address PK, rug_count, reputation_score)
      (:Wallet)-[:DEPLOYED]->(:Deployer)
      (:Wallet)-[:TRANSFERRED {value}]->(:Wallet)
    """
    import httpx

    db = _db()
    conn = kuzu.Connection(db)
    for stmt in (
        "CREATE NODE TABLE IF NOT EXISTS Wallet(address STRING, label STRING, tier STRING, PRIMARY KEY(address))",
        "CREATE NODE TABLE IF NOT EXISTS Deployer(address STRING, rug_count INT64, reputation_score DOUBLE, PRIMARY KEY(address))",
        "CREATE REL TABLE IF NOT EXISTS DEPLOYED(FROM Wallet TO Deployer)",
        "CREATE REL TABLE IF NOT EXISTS TRANSFERRED(FROM Wallet TO Wallet, value DOUBLE)",
    ):
        conn.execute(stmt)

    def ch(sql: str) -> list[tuple[str, ...]]:
        r = httpx.post(
            f"{CH_URL}/?user={CH_USER}&password={CH_PASSWORD}",
            content=sql + " FORMAT TSV",
            timeout=120,
        )
        r.raise_for_status()
        return [tuple(line.split("\t")) for line in r.text.strip().splitlines() if line]

    # Deployers + their rug counts.
    rows = ch("SELECT address, count() AS rugs FROM wallet_memory.deployer_events GROUP BY address")
    for address, rugs in rows:
        conn.execute(
            "MERGE (d:Deployer {address: $a}) SET d.rug_count = $r",
            {"a": address, "r": int(rugs or 0)},
        )

    # Labels -> Wallet nodes (entity_labels is the curated label store).
    labels = ch(
        "SELECT address, any(name) FROM wallet_memory.entity_labels WHERE name != '' GROUP BY address LIMIT 500000"
    )
    for address, name in labels:
        conn.execute(
            "MERGE (w:Wallet {address: $a}) SET w.label = $n",
            {"a": address, "n": name},
        )

    # Deployer wallets.
    deploys = ch("SELECT DISTINCT deployer, address FROM wallet_memory.deployer_events LIMIT 500000")
    for deployer, token in deploys:
        conn.execute("MERGE (w:Wallet {address: $d})", {"d": deployer})
        conn.execute(
            "MATCH (w:Wallet {address: $d}), (t:Deployer {address: $t}) MERGE (w)-[:DEPLOYED]->(t)",
            {"d": deployer, "t": token},
        )

    count = _row(_query(conn, "MATCH (w:Wallet) RETURN count(w)"))[0]
    return int(count)


def wallet(address: str) -> dict[str, Any] | None:
    conn = kuzu.Connection(_db())
    r = _query(
        conn,
        "MATCH (w:Wallet {address: $a}) RETURN w.address, w.label, w.tier",
        {"a": address},
    )
    if not r.has_next():
        return None
    addr, label, tier = _row(r)
    return {"address": addr, "label": label, "tier": tier}


def top_deployers(min_rugs: int = 1, limit: int = 200) -> list[dict[str, Any]]:
    conn = kuzu.Connection(_db())
    r = _query(
        conn,
        "MATCH (d:Deployer) WHERE d.rug_count >= $m "
        "RETURN d.address, d.rug_count, d.reputation_score "
        "ORDER BY d.rug_count DESC LIMIT $l",
        {"m": min_rugs, "l": limit},
    )
    out: list[dict[str, Any]] = []
    while r.has_next():
        addr, rugs, rep = _row(r)
        out.append({"address": addr, "rug_count": rugs, "reputation": rep})
    return out


def fund_flow(start: str, max_hops: int = 3, limit: int = 100) -> list[dict[str, Any]]:
    """Paths of transfers out of a wallet, up to ``max_hops``."""
    conn = kuzu.Connection(_db())
    r = _query(
        conn,
        f"MATCH p = (a:Wallet {{address: $a}})-[:TRANSFERRED*1..{int(max_hops)}]->(b) "
        "RETURN b.address, length(p) ORDER BY length(p) LIMIT $l",
        {"a": start, "l": limit},
    )
    out: list[dict[str, Any]] = []
    while r.has_next():
        addr, hops = _row(r)
        out.append({"address": addr, "hops": hops})
    return out
