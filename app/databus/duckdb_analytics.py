"""
DuckDB Offline Analytics Engine
=================================

Local forensic analytics on cinnabox — no VPS needed.
Loads Real-CATS (153K addresses) and MBAL (10M addresses) into
DuckDB for instant SQL queries, risk scoring, and label lookups.

Tables:
  - criminal_addresses: Real-CATS criminal + supplementary
  - benign_addresses: Real-CATS benign
  - mbal_labels: 10M multi-chain labeled addresses
  - address_index: Unified search index across all datasets
"""

import contextlib
import logging
import os
import time
from typing import Any

import duckdb

logger = logging.getLogger("databus.duckdb_analytics")

# ── Path discovery ────────────────────────────────────────────────

DB_PATH = os.path.expanduser("~/rmi/analytics.duckdb")

REAL_CATS_DIRS = [
    os.path.expanduser("~/rmi/Real-CATS"),
    os.path.expanduser("~/rmi/datasets/Real-CATS"),
    "/tmp/Real-CATS",
    "/app/Real-CATS",
]

MBAL_DIRS = [
    os.path.expanduser("~/rmi/mbal"),
    os.path.expanduser("~/rmi/datasets/mbal"),
    "/tmp/mbal",
    "/app/mbal",
]

# ── Schema DDL ───────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS criminal_addresses (
    address VARCHAR,
    chain VARCHAR DEFAULT 'unknown',
    label VARCHAR DEFAULT 'criminal',
    source VARCHAR DEFAULT 'real-cats',
    loaded_at TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS benign_addresses (
    address VARCHAR,
    chain VARCHAR DEFAULT 'unknown',
    label VARCHAR DEFAULT 'benign',
    source VARCHAR DEFAULT 'real-cats',
    loaded_at TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS mbal_labels (
    address VARCHAR,
    chain VARCHAR,
    category VARCHAR,
    label VARCHAR,
    source VARCHAR DEFAULT 'mbal',
    loaded_at TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS address_index (
    address VARCHAR,
    chain VARCHAR,
    label VARCHAR,
    category VARCHAR,
    risk_score DOUBLE DEFAULT 0.0,
    source VARCHAR
);
"""

# ── Data loading ────────────────────────────────────────────────


def _find_dir(candidates: list[str]) -> str | None:
    for d in candidates:
        if os.path.isdir(d) and os.listdir(d):
            return d
    return None


def _load_real_cats(con, base_dir: str) -> dict:
    """Load Real-CATS dataset into criminal_addresses and benign_addresses."""
    stats = {"criminal": 0, "benign": 0, "errors": []}

    file_map = {
        "CB.tsv": ("criminal", "bitcoin"),
        "CE.tsv": ("criminal", "ethereum"),
        "BB.tsv": ("benign", "bitcoin"),
        "BE.tsv": ("benign", "ethereum"),
        "Sup-CATS.tsv": ("criminal", "multi"),
        "TI_M.tsv": ("criminal", "multi"),
        "TI_B.tsv": ("benign", "multi"),
    }

    for fname, (label_type, chain) in file_map.items():
        fpath = os.path.join(base_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            table = "criminal_addresses" if label_type == "criminal" else "benign_addresses"
            con.execute(f"""
                INSERT INTO {table} (address, chain, label, source)
                SELECT col1, '{chain}', '{label_type}', 'real-cats'
                FROM read_csv_auto('{fpath}', delim='\\t', header=true, all_varchar=true,
                                    sample_size=50000)
                WHERE col1 IS NOT NULL AND col1 != ''
            """)
            count = con.execute("SELECT changes()").fetchone()[0]
            stats[label_type] += count if count else 0
        except Exception:
            # Fallback: try with first column as address
            try:
                con.execute(f"""
                    INSERT INTO {table} (address, chain, label, source)
                    SELECT column0, '{chain}', '{label_type}', 'real-cats'
                    FROM read_csv_auto('{fpath}', delim='\\t', header=false, all_varchar=true)
                    WHERE column0 IS NOT NULL AND column0 != ''
                """)
                stats[label_type] += 1
            except Exception as e2:
                stats["errors"].append(f"{fname}: {e2}")

    # Also load Identifier.tsv for address mapping
    id_path = os.path.join(base_dir, "Identifier.tsv")
    if os.path.isfile(id_path):
        with contextlib.suppress(Exception):
            con.execute(f"""
                INSERT INTO criminal_addresses (address, chain, label, source)
                SELECT col1, 'multi', 'criminal-identifier', 'real-cats-ids'
                FROM read_csv_auto('{id_path}', delim='\\t', header=true, all_varchar=true)
                WHERE col1 IS NOT NULL AND col1 != ''
            """)

    return stats


def _load_mbal(con, base_dir: str) -> dict:
    """Load MBAL 10M address labels into mbal_labels."""
    stats = {"loaded": 0, "errors": []}

    # Primary dataset - load with column mapping
    primary = os.path.join(base_dir, "dataset_10m_ads.csv")
    if os.path.isfile(primary):
        try:
            start = time.time()
            # Columns: chain,address,categories,entity,source
            con.execute(f"""
                INSERT INTO mbal_labels (address, chain, category, label, source)
                SELECT
                    address,
                    COALESCE(chain, 'unknown'),
                    COALESCE(categories, ''),
                    COALESCE(entity, COALESCE(categories, '')),
                    'mbal-10m'
                FROM read_csv_auto('{primary}',
                                    header=true,
                                    all_varchar=true,
                                    sample_size=50000)
                WHERE address IS NOT NULL AND address != ''
            """)
            elapsed = time.time() - start
            count = con.execute(
                "SELECT COUNT(*) FROM mbal_labels WHERE source='mbal-10m'"
            ).fetchone()[0]
            stats["loaded"] = count
            stats["time_s"] = round(elapsed, 1)
        except Exception:
            # Try simpler approach - just grab first column as address
            try:
                con.execute(f"""
                    INSERT INTO mbal_labels (address, chain, category, label, source)
                    SELECT
                        column0,
                        'unknown',
                        'unknown',
                        'unknown',
                        'mbal-10m'
                    FROM read_csv_auto('{primary}',
                                        header=false,
                                        all_varchar=true,
                                        sample_size=100000)
                    WHERE column0 IS NOT NULL AND column0 != ''
                    LIMIT 5000000
                """)
                count = con.execute(
                    "SELECT COUNT(*) FROM mbal_labels WHERE source='mbal-10m'"
                ).fetchone()[0]
                stats["loaded"] = count
            except Exception as e2:
                stats["errors"].append(f"mbal-10m: {e2}")

    # Training/test splits (smaller, faster)
    for fname in os.listdir(base_dir):
        if not fname.endswith(".csv") or fname == "dataset_10m_ads.csv":
            continue
        fpath = os.path.join(base_dir, fname)
        tag = fname.replace(".csv", "")[:30]
        try:
            con.execute(f"""
                INSERT INTO mbal_labels (address, chain, category, label, source)
                SELECT
                    column0, 'unknown', '{tag}', '{tag}', 'mbal-{tag}'
                FROM read_csv_auto('{fpath}', header=true, all_varchar=true,
                                    sample_size=50000)
                WHERE column0 IS NOT NULL AND column0 != ''
                LIMIT 500000
            """)
            c = con.execute(
                f"SELECT COUNT(*) FROM mbal_labels WHERE source='mbal-{tag}'"
            ).fetchone()[0]
            stats["loaded"] += c
        except Exception:
            pass

    return stats


def _build_index(con):
    """Build unified address_index from all loaded data."""
    con.execute("DELETE FROM address_index")

    # Criminal addresses → high risk
    con.execute("""
        INSERT INTO address_index (address, chain, label, category, risk_score, source)
        SELECT address, chain, label, 'criminal', 0.95, source
        FROM criminal_addresses
        WHERE address IS NOT NULL AND address != ''
    """)

    # Benign addresses → low risk
    con.execute("""
        INSERT INTO address_index (address, chain, label, category, risk_score, source)
        SELECT address, chain, label, 'benign', 0.05, source
        FROM benign_addresses
        WHERE address IS NOT NULL AND address != ''
    """)

    # MBAL labels → risk based on category
    con.execute("""
        INSERT INTO address_index (address, chain, label, category, risk_score, source)
        SELECT
            address,
            chain,
            label,
            category,
            CASE
                WHEN LOWER(category) LIKE '%scam%' THEN 0.95
                WHEN LOWER(category) LIKE '%phish%' THEN 0.93
                WHEN LOWER(category) LIKE '%hack%' THEN 0.90
                WHEN LOWER(category) LIKE '%ransom%' THEN 0.92
                WHEN LOWER(category) LIKE '%mixer%' THEN 0.80
                WHEN LOWER(category) LIKE '%gambl%' THEN 0.60
                WHEN LOWER(category) LIKE '%exchange%' THEN 0.10
                WHEN LOWER(category) LIKE '%miner%' THEN 0.20
                WHEN LOWER(category) LIKE '%service%' THEN 0.15
                WHEN LOWER(category) LIKE '%wallet%' THEN 0.10
                ELSE 0.50
            END,
            source
        FROM mbal_labels
        WHERE address IS NOT NULL AND address != ''
          AND (address, source) NOT IN (
              SELECT address, source FROM address_index
          )
    """)

    # Create search index
    try:
        con.execute("DROP INDEX IF EXISTS idx_address")
        con.execute("CREATE INDEX idx_address ON address_index (address)")
    except Exception:
        pass

    try:
        con.execute("DROP INDEX IF EXISTS idx_chain")
        con.execute("CREATE INDEX idx_chain ON address_index (chain)")
    except Exception:
        pass


# ── Public API ───────────────────────────────────────────────────


class DuckDBAnalytics:
    """Offline analytics engine using DuckDB on cinnabox."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._con = None
        self._loaded = False

    def connect(self):
        if self._con is None:
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
            self._con = duckdb.connect(self.db_path)
        return self._con

    def initialize(self, force_reload: bool = False) -> dict:
        """Create tables and load data. Returns load stats."""
        con = self.connect()

        # Check if already loaded
        if not force_reload:
            try:
                count = con.execute("SELECT COUNT(*) FROM address_index").fetchone()[0]
                if count > 0:
                    self._loaded = True
                    return {
                        "status": "already_loaded",
                        "total_indexed": count,
                        "tables": {
                            "criminal": con.execute(
                                "SELECT COUNT(*) FROM criminal_addresses"
                            ).fetchone()[0],
                            "benign": con.execute(
                                "SELECT COUNT(*) FROM benign_addresses"
                            ).fetchone()[0],
                            "mbal": con.execute("SELECT COUNT(*) FROM mbal_labels").fetchone()[0],
                            "index": count,
                        },
                    }
            except Exception:
                pass

        # Create schema
        con.execute(SCHEMA_SQL)

        stats: dict[str, Any] = {"status": "loaded", "tables": {}}

        # Load Real-CATS
        cats_dir = _find_dir(REAL_CATS_DIRS)
        if cats_dir:
            cats_stats = _load_real_cats(con, cats_dir)
            stats["tables"]["criminal"] = con.execute(
                "SELECT COUNT(*) FROM criminal_addresses"
            ).fetchone()[0]
            stats["tables"]["benign"] = con.execute(
                "SELECT COUNT(*) FROM benign_addresses"
            ).fetchone()[0]
            stats["real_cats"] = cats_stats
        else:
            stats["tables"]["criminal"] = 0
            stats["tables"]["benign"] = 0
            stats["real_cats"] = {"skipped": "directory not found"}

        # Load MBAL
        mbal_dir = _find_dir(MBAL_DIRS)
        if mbal_dir:
            mbal_stats = _load_mbal(con, mbal_dir)
            stats["tables"]["mbal"] = con.execute("SELECT COUNT(*) FROM mbal_labels").fetchone()[0]
            stats["mbal"] = mbal_stats
        else:
            stats["tables"]["mbal"] = 0
            stats["mbal"] = {"skipped": "directory not found"}

        # Build unified index
        _build_index(con)
        stats["tables"]["index"] = con.execute("SELECT COUNT(*) FROM address_index").fetchone()[0]

        self._loaded = True
        return stats

    # ── Query methods ────────────────────────────────────────────

    def lookup_address(self, address: str) -> dict | None:
        """Look up a single address across all datasets."""
        con = self.connect()
        if not self._loaded:
            self.initialize()

        results = con.execute(
            """
            SELECT address, chain, label, category, risk_score, source
            FROM address_index
            WHERE LOWER(address) = LOWER(?)
        """,
            [address],
        ).fetchall()

        if not results:
            return None

        entries = []
        for row in results:
            entries.append(
                {
                    "address": row[0],
                    "chain": row[1],
                    "label": row[2],
                    "category": row[3],
                    "risk_score": float(row[4]) if row[4] else 0.0,
                    "source": row[5],
                }
            )

        # Return highest risk entry first
        entries.sort(key=lambda x: x["risk_score"], reverse=True)
        return {
            "address": address,
            "matches": len(entries),
            "best_label": entries[0]["label"],
            "risk_score": entries[0]["risk_score"],
            "chain": entries[0]["chain"],
            "sources": list({e["source"] for e in entries}),
            "all_labels": entries,
        }

    def batch_lookup(self, addresses: list[str]) -> list[dict]:
        """Batch look up multiple addresses."""
        con = self.connect()
        if not self._loaded:
            self.initialize()

        if not addresses:
            return []

        placeholders = ",".join("?" * len(addresses))
        rows = con.execute(
            f"""
            SELECT address, chain, label, category, risk_score, source
            FROM address_index
            WHERE LOWER(address) IN ({placeholders})
        """,
            [a.lower() for a in addresses],
        ).fetchall()

        # Group by address
        by_addr: dict[str, list] = {}
        for row in rows:
            addr = row[0]
            by_addr.setdefault(addr.lower(), []).append(
                {
                    "address": row[0],
                    "chain": row[1],
                    "label": row[2],
                    "category": row[3],
                    "risk_score": float(row[4]) if row[4] else 0.0,
                    "source": row[5],
                }
            )

        results = []
        for addr in addresses:
            entries = by_addr.get(addr.lower(), [])
            if entries:
                entries.sort(key=lambda x: x["risk_score"], reverse=True)
                results.append(
                    {
                        "address": addr,
                        "found": True,
                        "risk_score": entries[0]["risk_score"],
                        "best_label": entries[0]["label"],
                        "chain": entries[0]["chain"],
                        "total_matches": len(entries),
                    }
                )
            else:
                results.append(
                    {
                        "address": addr,
                        "found": False,
                        "risk_score": 0.0,
                        "best_label": "unknown",
                        "chain": "unknown",
                        "total_matches": 0,
                    }
                )

        return results

    def risk_score(self, address: str) -> float:
        """Get risk score for an address (0.0-1.0)."""
        result = self.lookup_address(address)
        if result:
            return result["risk_score"]
        return 0.0  # Unknown = no risk signal

    def search_labels(self, query: str, chain: str | None = None, limit: int = 50) -> list[dict]:
        """Search labels by keyword."""
        con = self.connect()
        if not self._loaded:
            self.initialize()

        sql = """
            SELECT address, chain, label, category, risk_score, source
            FROM address_index
            WHERE (LOWER(label) LIKE '%' || LOWER(?) || '%'
                OR LOWER(category) LIKE '%' || LOWER(?) || '%')
        """
        params = [query, query]
        if chain:
            sql += " AND LOWER(chain) = LOWER(?)"
            params.append(chain)
        sql += f" ORDER BY risk_score DESC LIMIT {limit}"

        rows = con.execute(sql, params).fetchall()
        return [
            {
                "address": row[0],
                "chain": row[1],
                "label": row[2],
                "category": row[3],
                "risk_score": float(row[4]) if row[4] else 0.0,
                "source": row[5],
            }
            for row in rows
        ]

    def stats(self) -> dict:
        """Get database statistics."""
        con = self.connect()
        try:
            return {
                "criminal_addresses": con.execute(
                    "SELECT COUNT(*) FROM criminal_addresses"
                ).fetchone()[0],
                "benign_addresses": con.execute("SELECT COUNT(*) FROM benign_addresses").fetchone()[
                    0
                ],
                "mbal_labels": con.execute("SELECT COUNT(*) FROM mbal_labels").fetchone()[0],
                "indexed_addresses": con.execute("SELECT COUNT(*) FROM address_index").fetchone()[
                    0
                ],
                "chains": con.execute(
                    "SELECT DISTINCT chain FROM address_index WHERE chain IS NOT NULL"
                ).fetchall(),
                "categories": con.execute("""
                    SELECT category, COUNT(*) as cnt
                    FROM address_index
                    WHERE category IS NOT NULL AND category != ''
                    GROUP BY category ORDER BY cnt DESC LIMIT 20
                """).fetchall(),
                "db_size_mb": round(os.path.getsize(self.db_path) / 1024 / 1024, 1)
                if os.path.exists(self.db_path)
                else 0,
            }
        except Exception as e:
            return {"error": str(e), "initialized": self._loaded}

    def execute_query(self, sql: str, params: list | None = None) -> list[tuple]:
        """Run arbitrary SQL query. For advanced analytics."""
        con = self.connect()
        if params:
            return con.execute(sql, params).fetchall()
        return con.execute(sql).fetchall()

    def close(self):
        if self._con:
            self._con.close()
            self._con = None


# ── DataBus provider functions ───────────────────────────────────

_engine: DuckDBAnalytics | None = None


def _get_engine() -> DuckDBAnalytics:
    global _engine
    if _engine is None:
        _engine = DuckDBAnalytics()
        _engine.initialize()
    return _engine


async def _duckdb_lookup(address: str = "", **kwargs) -> dict | None:
    """Look up address in local DuckDB analytics."""
    if not address:
        return None
    engine = _get_engine()
    return engine.lookup_address(address)


async def _duckdb_batch_lookup(addresses: list | None = None, **kwargs) -> dict | None:
    """Batch look up addresses in local DuckDB analytics."""
    if not addresses:
        return None
    engine = _get_engine()
    results = engine.batch_lookup(addresses)
    return {
        "results": results,
        "total": len(results),
        "found": sum(1 for r in results if r["found"]),
    }


async def _duckdb_risk_score(address: str = "", **kwargs) -> dict | None:
    """Get risk score for an address."""
    if not address:
        return None
    engine = _get_engine()
    score = engine.risk_score(address)
    return {"address": address, "risk_score": score, "source": "duckdb_offline"}


async def _duckdb_search_labels(
    query: str = "", chain: str | None = None, limit: int = 50, **kwargs
) -> dict | None:
    """Search labels by keyword."""
    if not query:
        return None
    engine = _get_engine()
    results = engine.search_labels(query, chain, limit)
    return {"query": query, "chain": chain, "results": results, "count": len(results)}


async def _duckdb_stats(**kwargs) -> dict | None:
    """Get DuckDB analytics statistics."""
    engine = _get_engine()
    return engine.stats()


async def _duckdb_query(sql: str = "", **kwargs) -> dict | None:
    """Run arbitrary SQL on DuckDB (admin only)."""
    if not sql:
        return {"error": "SQL query required"}
    # Safety: only SELECT allowed
    if not sql.strip().upper().startswith("SELECT"):
        return {"error": "Only SELECT queries allowed"}
    engine = _get_engine()
    try:
        rows = engine.execute_query(sql)
        return {"sql": sql, "rows": len(rows), "data": rows[:100], "truncated": len(rows) > 100}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import asyncio
    import json

    async def test():
        logger.info("Initializing DuckDB analytics...")
        engine = DuckDBAnalytics()
        stats = engine.initialize()
        logger.info(f"Load stats: {json.dumps(stats, indent=2, default=str)}")
        logger.info("\nDatabase stats:")
        db_stats = engine.stats()
        logger.info(json.dumps(db_stats, indent=2, default=str))
        # Test lookups
        logger.info("\nTest lookups:")
        test_addrs = [
            "1A1zP1eP5QGefi2DMPTftTL5SLmv7DivfNa",  # Satoshi
            "0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe",  # Ethereum Foundation
            "3FZbgi29cpjq2CAjQR8gRXjDQnQjNzLZgE",  # unknown
        ]
        for addr in test_addrs:
            result = engine.lookup_address(addr)
            if result:
                print(
                    f"  {addr[:20]}... → risk={result['risk_score']:.2f} label={result['best_label']}"
                )
            else:
                logger.info(f"  {addr[:20]}... → not found")
        # Test label search
        logger.info("\nSearch 'exchange':")
        exchanges = engine.search_labels("exchange", limit=5)
        for ex in exchanges:
            logger.info(f"  {ex['address'][:20]}... {ex['label']} risk={ex['risk_score']:.2f}")
        engine.close()

    asyncio.run(test())
