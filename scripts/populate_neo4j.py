#!/usr/bin/env python3
"""
Populate Neo4j from ClickHouse wallet_memory.wallet_labels (~154k curated labels).

Usage:
    python scripts/populate_neo4j.py                    # Phase 1: top 100K addresses by label count
    python scripts/populate_neo4j.py --phase 2          # Phase 2: exchange-labeled addresses
    python scripts/populate_neo4j.py --phase 3          # Phase 3: scam/phishing addresses
    python scripts/populate_neo4j.py --phase all        # Everything (be careful!)
    python scripts/populate_neo4j.py --limit 10000      # Custom limit

Schema:
    (Address:Wallet {address, chain}) -[:LABELED_AS {source, confidence}]-> (Label {name, category})
    (Address)-[:INTERACTS_WITH]->(Address)  # If tx data available (future phases)

Idempotent: uses MERGE not CREATE — safe to re-run.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "rugmunch-neo4j-2026")

CH_HOST = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "localhost")
CH_PORT = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT", "9000"))
CH_USER = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD", "")
CH_DB = os.getenv("CLICKHOUSE_DATABASE", "wallet_memory")

BATCH_SIZE = 500
PROGRESS_INTERVAL = 10_000


def get_ch_client() -> Any:
    """Create ClickHouse native protocol client."""
    try:
        from clickhouse_driver import Client as CHClient

        client = CHClient(
            host=CH_HOST,
            port=CH_PORT,
            user=CH_USER,
            password=CH_PASSWORD or "",
            database=CH_DB,
            connect_timeout=10,
            send_receive_timeout=30,
        )
        client.execute("SELECT 1")
        print(f"✓ ClickHouse connected: {CH_HOST}:{CH_PORT}/{CH_DB}")
        return client
    except ImportError:
        print("✗ clickhouse_driver not installed. Run: pip install clickhouse-driver")
        sys.exit(1)
    except Exception as e:
        print(f"✗ ClickHouse connection failed: {e}")
        sys.exit(1)


def get_neo4j_driver() -> Any:
    """Create Neo4j Bolt driver."""
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        # Verify connectivity
        with driver.session() as s:
            result = s.run("RETURN 1 AS ok").single()
            assert result is not None
            assert result["ok"] == 1
        print(f"✓ Neo4j connected: {NEO4J_URI}")
        return driver
    except ImportError:
        print("✗ neo4j driver not installed. Run: pip install neo4j")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Neo4j connection failed: {e}")
        sys.exit(1)


def create_constraints(driver: Any) -> None:
    """Create uniqueness constraints for MERGE performance."""
    with driver.session() as s:
        try:
            s.run("CREATE CONSTRAINT wallet_address IF NOT EXISTS FOR (w:Wallet) REQUIRE w.address IS UNIQUE")
            print("✓ Constraint: Wallet.address UNIQUE")
        except Exception as e:
            print(f"  (constraint may already exist: {e})")

        try:
            s.run("CREATE CONSTRAINT label_name IF NOT EXISTS FOR (l:Label) REQUIRE l.name IS UNIQUE")
            print("✓ Constraint: Label.name UNIQUE")
        except Exception as e:
            print(f"  (constraint may already exist: {e}")

    # Create indexes for common query patterns
    with driver.session() as s:
        try:
            s.run("CREATE INDEX wallet_chain IF NOT EXISTS FOR (w:Wallet) ON (w.chain)")
            print("✓ Index: Wallet.chain")
        except Exception as e:
            print(f"  (index may already exist: {e}")
        try:
            s.run("CREATE INDEX label_category IF NOT EXISTS FOR (l:Label) ON (l.category)")
            print("✓ Index: Label.category")
        except Exception as e:
            print(f"  (index may already exist: {e}")


def get_top_addresses_by_label_count(
    ch_client: Any,
    limit: int = 100_000,
    phase_filter: str | None = None,
) -> list[tuple[str, int]]:
    """Get addresses with the most labels, optionally filtered by category.

    Returns list of (address, label_count) tuples.
    """
    categories: list[str] | None = None
    if phase_filter == "exchange":
        print("Phase 2: Filtering for exchange/cex labels...")
        categories = ["exchange", "cex", "CEX", "Exchange"]
    elif phase_filter == "scam":
        print("Phase 3: Filtering for scam/phishing/hack labels...")
        categories = ["scam", "phishing", "hack", "fraud", "exploit", "Scam", "Phishing"]
    elif phase_filter == "sanction":
        print("Phase S: Filtering for sanctioned/OFAC labels...")
        categories = ["sanction", "sanctioned", "ofac", "OFAC", "Sanctioned"]

    if categories:
        query = """
            SELECT address, count() AS label_count
            FROM wallet_memory.wallet_labels
            WHERE label_category IN %(categories)s
            GROUP BY address
            ORDER BY label_count DESC
            LIMIT %(lim)s
        """
        rows = ch_client.execute(query, {"categories": tuple(categories), "lim": limit})
    else:
        query = """
            SELECT address, count() AS label_count
            FROM wallet_memory.wallet_labels
            GROUP BY address
            ORDER BY label_count DESC
            LIMIT %(lim)s
        """
        rows = ch_client.execute(query, {"lim": limit})
    print(f"  → {len(rows)} addresses returned")
    return [(r[0], r[1]) for r in rows]


def get_labels_for_addresses(ch_client: Any, addresses: list[str]) -> list[dict]:
    """Fetch all labels for a batch of addresses from ClickHouse.

    Returns list of dicts with keys: address, chain_id, label_name, label_category, source,
    is_sanctioned, loaded_at, label_subtype
    """
    if not addresses:
        return []

    query = """
        SELECT address, chain_id, label_name, label_category, source,
               is_sanctioned, loaded_at, label_subtype
        FROM wallet_memory.wallet_labels
        WHERE address IN %(addresses)s
        ORDER BY loaded_at DESC
    """
    rows = ch_client.execute(query, {"addresses": tuple(addresses)})
    return [
        {
            "address": r[0].lower(),
            "chain_id": r[1] or "",
            "label_name": (r[2] or "").strip(),
            "label_category": (r[3] or "").strip(),
            "source": (r[4] or "").strip(),
            "is_sanctioned": bool(r[5]) if len(r) > 5 else False,
            "loaded_at": r[6] if len(r) > 6 else None,
            "label_subtype": (r[7] or "").strip() if len(r) > 7 else "",
        }
        for r in rows
    ]


def _chain_id_to_name(chain_id: str) -> str:
    """Map ClickHouse chain_id to human-readable chain name."""
    mapping = {
        "1": "ethereum",
        "ethereum": "ethereum",
        "eth": "ethereum",
        "56": "bsc",
        "bsc": "bsc",
        "bnb": "bsc",
        "137": "polygon",
        "polygon": "polygon",
        "matic": "polygon",
        "42161": "arbitrum",
        "arbitrum": "arbitrum",
        "10": "optimism",
        "optimism": "optimism",
        "8453": "base",
        "base": "base",
        "43114": "avalanche",
        "avalanche": "avalanche",
        "avax": "avalanche",
        "250": "fantom",
        "fantom": "fantom",
        "ftm": "fantom",
        "100": "gnosis",
        "gnosis": "gnosis",
        "xdai": "gnosis",
        "tron": "tron",
        "trx": "tron",
        "bitcoin": "bitcoin",
        "btc": "bitcoin",
        "solana": "solana",
        "101": "solana",
        "SOL": "solana",
    }
    return mapping.get(chain_id, chain_id)


def batch_upsert_labels(driver: Any, labels: list[dict]) -> int:
    """Upsert a batch of address→label relationships into Neo4j using UNWIND.

    Uses MERGE for idempotency. Processes ALL labels in a single Cypher query
    for ~100x better throughput than individual queries.
    Returns count of relationships created.
    """
    if not labels:
        return 0

    # Deduplicate by (address, chain_id, label_name, source)
    seen = set()
    deduped: list[dict] = []
    for lbl in labels:
        if not lbl["label_name"]:
            continue
        key = (lbl["address"], lbl["chain_id"], lbl["label_name"], lbl["source"])
        if key not in seen:
            seen.add(key)
            # Resolve chain_id → chain name once
            lbl["chain"] = _chain_id_to_name(lbl["chain_id"])
            deduped.append(lbl)

    if not deduped:
        return 0

    # Build parameter list for UNWIND
    params = []
    for lbl in deduped:
        params.append(
            {
                "address": lbl["address"],
                "chain": lbl["chain"],
                "label_name": lbl["label_name"],
                "label_category": lbl["label_category"] or "unknown",
                "source": lbl["source"] or "clickhouse",
                "is_sanctioned": lbl["is_sanctioned"],
            }
        )

    query = """
    UNWIND $rows AS row
    MERGE (w:Wallet {address: row.address})
    SET w.chain = CASE
        WHEN w.chain IS NULL THEN row.chain
        WHEN w.chain <> row.chain AND w.chain <> 'multi' THEN 'multi'
        ELSE w.chain
    END,
    w.last_seen = datetime()
    WITH w, row
    MERGE (l:Label {name: row.label_name})
    SET l.category = row.label_category,
        l.source = CASE
            WHEN l.source IS NULL THEN row.source
            WHEN NOT l.source CONTAINS row.source THEN l.source + ',' + row.source
            ELSE l.source
        END
    WITH w, l, row
    MERGE (w)-[r:LABELED_AS]->(l)
    SET r.source = row.source,
        r.confidence = 0.95,
        r.is_sanctioned = row.is_sanctioned,
        r.last_updated = datetime()
    RETURN count(r) AS created
    """

    try:
        with driver.session() as s:
            result = s.run(query, rows=params)
            record = result.single()
            return record["created"] if record else 0
    except Exception as e:
        print(f"  ⚠ Neo4j batch error ({len(deduped)} labels): {e}")
        return 0


def get_total_labels(ch_client: Any) -> int:
    """Get total label count from ClickHouse."""
    rows = ch_client.execute("SELECT count() FROM wallet_memory.wallet_labels")
    return rows[0][0] if rows else 0


def print_stats(driver: Any) -> None:
    """Print Neo4j graph statistics."""
    with driver.session() as s:
        wallets = s.run("MATCH (w:Wallet) RETURN count(w) AS c").single()["c"]
        labels = s.run("MATCH (l:Label) RETURN count(l) AS c").single()["c"]
        rels = s.run("MATCH ()-[r:LABELED_AS]->() RETURN count(r) AS c").single()["c"]

    print("\n📊 Neo4j Graph Stats:")
    print(f"   Wallet nodes:    {wallets:>10,}")
    print(f"   Label nodes:     {labels:>10,}")
    print(f"   LABELED_AS rels: {rels:>10,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate Neo4j from ClickHouse wallet_labels")
    parser.add_argument(
        "--phase",
        type=str,
        default="1",
        choices=["1", "2", "3", "all"],
        help="Phase: 1=top addresses, 2=exchange, 3=scam, all=everything",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Override address limit (default: 100K for phase 1, 50K for 2/3)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Addresses per batch (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would be imported without writing to Neo4j",
    )
    args = parser.parse_args()

    phase = args.phase
    user_limit = args.limit

    # Determine address limit per phase
    phase_limits = {"1": 100_000, "2": 50_000, "3": 50_000, "all": 500_000}
    limit = user_limit or phase_limits.get(phase, 100_000)

    phase_filters = {"1": None, "2": "exchange", "3": "scam", "all": None}

    print(f"{'=' * 60}")
    print("  Neo4j Population Script")
    print(f"  Phase: {phase} ({phase_filters[phase] or 'top addresses by label count'})")
    print(f"  Address limit: {limit:,}")
    print(f"  Batch size: {args.batch_size:,}")
    print(f"{'=' * 60}\n")

    # 1. Connect to databases
    ch = get_ch_client()
    total_labels = get_total_labels(ch)
    print(f"  ClickHouse wallet_labels: {total_labels:,}\n")

    driver = get_neo4j_driver()

    # 2. Create constraints (optional but recommended for performance)
    if not args.dry_run:
        create_constraints(driver)

    # 3. Get addresses to import
    t_start = time.time()
    phase_filter_key = phase_filters[phase]
    addresses = get_top_addresses_by_label_count(ch, limit=limit, phase_filter=phase_filter_key)
    if not addresses:
        print("✗ No addresses found. Exiting.")
        driver.close()
        sys.exit(0)

    print(f"\n  Total addresses to process: {len(addresses):,}")
    if args.dry_run:
        print("  DRY RUN: Would create Neo4j nodes and relationships.")
        driver.close()
        return

    # 4. Batch import
    total_rels = 0
    addresses_processed = 0

    print(f"\n{'─' * 60}")
    print("  Importing...")
    print(f"{'─' * 60}")

    for i in range(0, len(addresses), args.batch_size):
        batch_addrs = addresses[i : i + args.batch_size]
        addr_list = [a[0] for a in batch_addrs]

        # Fetch labels from ClickHouse for this batch
        labels = get_labels_for_addresses(ch, addr_list)

        # Upsert into Neo4j
        created = batch_upsert_labels(driver, labels)
        total_rels += created
        addresses_processed += len(batch_addrs)

        # Progress reporting
        if addresses_processed % PROGRESS_INTERVAL == 0 or addresses_processed == len(addresses):
            elapsed = time.time() - t_start
            rate = addresses_processed / elapsed if elapsed > 0 else 0
            print(
                f"  📍 {addresses_processed:>8,}/{len(addresses):,} addresses "
                f"({100 * addresses_processed // len(addresses):>2}%) "
                f"| {total_rels:,} rels created "
                f"| {rate:>.0f} addr/s"
            )

        # Brief pause between batches to avoid overloading Neo4j (every ~10K addresses)
        if addresses_processed % 10000 == 0 and addresses_processed > 0:
            time.sleep(0.5)

    # 5. Summary
    elapsed = time.time() - t_start
    print(f"\n{'=' * 60}")
    print("  ✅ Import complete!")
    print(f"  Addresses processed: {addresses_processed:,}")
    print(f"  Relationships created: {total_rels:,}")
    print(f"  Time: {elapsed:.1f}s ({addresses_processed / elapsed:.0f} addr/s)")
    print(f"{'=' * 60}\n")

    print_stats(driver)

    # 6. Verify via REST API
    print(f"\n{'─' * 60}")
    print("  Verification via Neo4j HTTP API:")
    print(f"{'─' * 60}")
    try:
        import httpx

        r = httpx.post(
            "http://localhost:7474/db/neo4j/tx/commit",
            auth=(NEO4J_USER, NEO4J_PASSWORD),
            json={
                "statements": [
                    {"statement": "MATCH (n) RETURN count(n) as count"},
                    {
                        "statement": "MATCH ()-[r]->() RETURN count(r) as count",
                        "resultDataContents": ["row"],
                    },
                    {
                        "statement": """
                        MATCH (l:Label)
                        RETURN l.category, count(*) as cnt
                        ORDER BY cnt DESC LIMIT 10
                        """,
                    },
                ]
            },
            timeout=15,
        )
        data = r.json()
        for idx, result in enumerate(data.get("results", [])):
            col = result["columns"][0]
            val = result["data"][0]["row"][0] if result["data"] else "N/A"
            print(f"  [{idx}] {col}: {val}")

        if len(data["results"]) > 2:
            print("\n  Top label categories:")
            for row in data["results"][2]["data"]:
                print(f"    {row['row'][0]:>20}: {row['row'][1]:>8,}")
    except Exception as e:
        print(f"  ⚠ HTTP verification failed: {e}")

    driver.close()
    ch.disconnect()

    print(f"\n{'=' * 60}")
    print("  Script location: scripts/populate_neo4j.py")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
