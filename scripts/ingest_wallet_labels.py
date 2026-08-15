#!/usr/bin/env python3
"""
Comprehensive Wallet Label Ingestion Pipeline
=============================================
Ingests wallet address labels from multiple public sources into ClickHouse
(wallet_memory.wallet_labels + wallet_memory.scam_addresses) and Redis.

Sources:
  1. Built-in: Known contract directories (Tornado Cash, Uniswap, 1inch, etc.)
  2. Etherscan Labels API — live labels per address (EVM chains)
  3. CryptoScamDB API — known scam addresses
  4. ChainAbuse API — community-reported scam addresses
  5. RolodETH — public GitHub label database
  6. walletLabels.xyz — public address label API
  7. bluepages.fyi — public address label API
  8. MetaMask eth-phishing-detect — phishing addresses (GitHub)
  9. Open Labels Initiative — community labels
  10. Internal scanner findings — deployers flagged by our scanner

Usage:
    # Ingest ALL sources for all chains
    python3 scripts/ingest_wallet_labels.py --all

    # Ingest specific source for specific chain
    python3 scripts/ingest_wallet_labels.py --source builtin_contracts --chain ethereum
    python3 scripts/ingest_wallet_labels.py --source etherscan_labels --chain ethereum
    python3 scripts/ingest_wallet_labels.py --source cryptoscamdb
    python3 scripts/ingest_wallet_labels.py --source chainabuse

    # Ingest all scam-label sources
    python3 scripts/ingest_wallet_labels.py --scam-sources

    # Show report of current label counts
    python3 scripts/ingest_wallet_labels.py --report

    # Run on a schedule (cron / systemd)
    0 6 * * * cd /srv/work/repos/rmi-backend && python3 scripts/ingest_wallet_labels.py --scam-sources --quiet
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any

# Add parent dir for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
import logging

import httpx

try:
    import yaml
except ImportError:  # pragma: no cover — pyyaml ships in the rmi-backend venv
    yaml = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ingest_wallet_labels")

# ---------------------------------------------------------------------------
# Constants — Known contract directories we can hardcode
# ---------------------------------------------------------------------------

# Tornado Cash mixer addresses (Ethereum mainnet)
TORNADO_CASH_ADDRESSES: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {"address": "0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc", "name": "Tornado.Cash: 0.1 ETH", "category": "mixer"},
        {"address": "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936", "name": "Tornado.Cash: 1 ETH", "category": "mixer"},
        {"address": "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf", "name": "Tornado.Cash: 10 ETH", "category": "mixer"},
        {"address": "0xa160cdab225685da1d56aa342ad8841c3b53f291", "name": "Tornado.Cash: 100 ETH", "category": "mixer"},
        {
            "address": "0xd4b88df4d29f5cedd6857912842cff3b20c8cfa3",
            "name": "Tornado.Cash: 0.1 ETH (old)",
            "category": "mixer",
        },
        {
            "address": "0xfd8610d20aa15b7b2e3be39b396a1bc3516c7144",
            "name": "Tornado.Cash: 1 ETH (old)",
            "category": "mixer",
        },
        {
            "address": "0x07687e702b410fa43f4cb4af7fa097918ffd2730",
            "name": "Tornado.Cash: 10 ETH (old)",
            "category": "mixer",
        },
        {
            "address": "0x23773e65ed146a459791799d01336db863f8cdc1",
            "name": "Tornado.Cash: 100 ETH (old)",
            "category": "mixer",
        },
    ],
    "bsc": [
        {"address": "0x1e34a77868e19a6647b1f2f47b51ed72dede95dd", "name": "Tornado.Cash: 10 BNB", "category": "mixer"},
        {"address": "0xdf231d99ff8b6c6cbf4e9b9a945cbacef9339178", "name": "Tornado.Cash: 100 BNB", "category": "mixer"},
    ],
}

# Uniswap Universal Router (Ethereum mainnet & L2s)
UNISWAP_ROUTERS: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {
            "address": "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",
            "name": "Uniswap: Universal Router v1",
            "category": "dex",
        },
        {
            "address": "0xeF1c6E67703c7BD7107eed8303Fbe6EC2554BF6B",
            "name": "Uniswap: Universal Router v2",
            "category": "dex",
        },
        {
            "address": "0xEf1c6E67703c7BD7107eed8303Fbe6EC2554BF6B",
            "name": "Uniswap: Universal Router v3",
            "category": "dex",
        },
    ],
    "arbitrum": [
        {
            "address": "0x4C60051384bd2d3C01bfc845Cf5F4b44bcbE9de5",
            "name": "Uniswap: Universal Router (Arbitrum)",
            "category": "dex",
        },
    ],
    "optimism": [
        {
            "address": "0x4C60051384bd2d3C01bfc845Cf5F4b44bcbE9de5",
            "name": "Uniswap: Universal Router (Optimism)",
            "category": "dex",
        },
    ],
    "base": [
        {
            "address": "0x198EF79F1f515f02dFE9e3115eD9fC07183f02fC",
            "name": "Uniswap: Universal Router (Base)",
            "category": "dex",
        },
    ],
}

# 1inch Aggregation Router v5+ (across chains)
INCH_ROUTERS: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {
            "address": "0x1111111254fb6c44bac0bed2854e76f90643097d",
            "name": "1inch: Aggregation Router v5",
            "category": "dex",
        },
        {
            "address": "0x1111111254eeb31977f5be12d5c7cee44f6e44e6",
            "name": "1inch: Aggregation Router v6",
            "category": "dex",
        },
    ],
    "bsc": [
        {
            "address": "0x1111111254fb6c44bac0bed2854e76f90643097d",
            "name": "1inch: Aggregation Router v5 (BSC)",
            "category": "dex",
        },
    ],
    "polygon": [
        {
            "address": "0x1111111254fb6c44bac0bed2854e76f90643097d",
            "name": "1inch: Aggregation Router v5 (Polygon)",
            "category": "dex",
        },
    ],
    "arbitrum": [
        {
            "address": "0x1111111254fb6c44bac0bed2854e76f90643097d",
            "name": "1inch: Aggregation Router v5 (Arbitrum)",
            "category": "dex",
        },
    ],
}

# Known DeFi protocol admin/multisig addresses
DEFI_ADMIN_ADDRESSES: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {
            "address": "0xbe2ab3d3d8f6a32b96414ebbd865d1b4f1a6042b",
            "name": "Uniswap: Timelock (short)",
            "category": "defi_admin",
        },
        {
            "address": "0x1a9c8182c09f50c8318d769245bea52c32be35bc",
            "name": "Uniswap: Timelock (long)",
            "category": "defi_admin",
        },
        {
            "address": "0x10a19e7ee7d7f8a52822f6817de8ea18204f2e4f",
            "name": "Uniswap: Governance",
            "category": "defi_admin",
        },
        {
            "address": "0x3d9819210a31b4961b30ef54be2aed79b9c9cd3b",
            "name": "Compound: Timelock",
            "category": "defi_admin",
        },
        {
            "address": "0x6d903f6003cca6255d85cca4d3b5e5146dc33925",
            "name": "Aave: Governance v2",
            "category": "defi_admin",
        },
        {
            "address": "0xee56e2b3d491590b5b31738cc34d5232f378a8d5",
            "name": "Lido: DAO Treasury",
            "category": "defi_admin",
        },
        {
            "address": "0x3e40d73eb977dc6a537af587d48316fee66e9c8c",
            "name": "MakerDAO: Governance",
            "category": "defi_admin",
        },
    ],
}

# Known Gnosis Safe multisigs (prominent)
GNOSIS_SAFE_ADDRESSES: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {
            "address": "0x7a1c6abdcf83a5821d65d1e1f34360bda27f554c",
            "name": "Gnosis Safe: Treasury",
            "category": "multisig",
        },
    ],
}

# ===========================================================================
# ClickHouse Storage Helpers
# ===========================================================================


def _get_ch_client() -> Any | None:
    """Get ClickHouse client (sync, for script use).

    Tries clickhouse-connect (HTTP) first, then clickhouse_driver (native).
    """
    # Read env vars with same naming as docker-compose + app defaults
    ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
    ch_port_raw = os.getenv("CLICKHOUSE_PORT", "8123")
    ch_user = os.getenv("CLICKHOUSE_USER", "rmi")
    ch_pass = os.getenv("CLICKHOUSE_PASSWORD", os.getenv("CH_PASSWORD", ""))
    ch_db = "wallet_memory"

    # Try clickhouse-connect (HTTP) first — more compatible with auth setups
    try:
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=ch_host,
            port=int(ch_port_raw),
            username=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=10,
        )
        client.query("SELECT 1")
        logger.info(f"ClickHouse connected via HTTP: {ch_host}:{ch_port_raw}/{ch_db} as {ch_user}")
        return client
    except ImportError:
        logger.debug("clickhouse-connect not available, trying clickhouse-driver")
    except Exception as e:
        logger.debug(f"clickhouse-connect failed: {e}")

    # Fallback: clickhouse_driver (native protocol, port 9000)
    try:
        from clickhouse_driver import Client as CHClient

        native_port = int(os.getenv("CLICKHOUSE_NATIVE_PORT", "9000"))
        client = CHClient(
            host=ch_host,
            port=native_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=5,
            send_receive_timeout=30,
        )
        client.execute("SELECT 1")
        logger.info(f"ClickHouse connected via native: {ch_host}:{native_port}/{ch_db} as {ch_user}")
        return client
    except ImportError:
        logger.error("Neither clickhouse-connect nor clickhouse-driver available")
        return None
    except Exception as e:
        logger.warning(f"ClickHouse native connection failed: {e}")
        return None


def _get_redis_client() -> Any | None:
    """Get Redis client.

    Reads env vars: REDIS_URL (preferred) or REDIS_HOST/PORT/PASSWORD/DB.
    Falls back gracefully if Redis is unavailable.
    """
    try:
        import redis as sync_redis

        redis_url = os.getenv("REDIS_URL", "")
        if redis_url:
            client = sync_redis.from_url(redis_url, decode_responses=True)
        else:
            r_host = os.getenv("REDIS_HOST", "localhost")
            r_port = int(os.getenv("REDIS_PORT", "6379"))
            r_pass = os.getenv("REDIS_PASSWORD", "")
            r_db = int(os.getenv("REDIS_DB", "0"))
            if r_pass:
                client = sync_redis.Redis(host=r_host, port=r_port, password=r_pass, db=r_db, decode_responses=True)
            else:
                client = sync_redis.Redis(host=r_host, port=r_port, db=r_db, decode_responses=True)
        client.ping()
        logger.info(f"Redis connected: {r_host}:{r_port}/{r_db}")
        return client
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")
        return None


# ===========================================================================
# ClickHouse abstraction helper
# ===========================================================================


def _ch_execute(ch: Any, query: str, params: list | None = None) -> Any:
    """Execute a ClickHouse INSERT, handling both client APIs.

    clickhouse-connect: use ch.insert() for batch inserts
    clickhouse_driver: use ch.execute() with list of tuples
    """
    if hasattr(ch, "query"):
        # clickhouse-connect API — detect INSERT vs other
        q_upper = query.strip().upper()
        if q_upper.startswith("INSERT"):
            # Parse table name from INSERT INTO <table>
            import re

            m = re.search(r"INTO\s+(\S+)", query, re.IGNORECASE)
            table = m.group(1) if m else None
            if table and params:
                col_names = list(params[0].keys())
                row_data = [tuple(p.values()) for p in params]
                return ch.insert(table, row_data, column_names=col_names)
            return None
        if params:
            return ch.query(query, parameters=params)
        return ch.query(query)
    # clickhouse_driver API
    return ch.execute(query, params or [])


def _ch_select(ch: Any, query: str, params: dict | None = None) -> list[Any]:
    """Run a SELECT and return rows."""
    if hasattr(ch, "query"):
        result = ch.query(query, parameters=params) if params else ch.query(query)
        return result.result_rows if result else []
    return ch.execute(query, params) if params else ch.execute(query)


# ===========================================================================
# Core ingestion helpers
# ===========================================================================


def _make_label_id(address: str, source: str, chain: str, name: str = "") -> str:
    """Stable hash-based ID for dedup."""
    raw = f"wallet_label:{address.lower()}:{source}:{chain.lower()}:{name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _batch_insert_wallet_labels(
    ch: Any,
    redis: Any | None,
    labels: list[dict[str, Any]],
    batch_size: int = 1000,
) -> int:
    """Batch insert labels into ClickHouse wallet_labels + Redis cache."""
    if not labels:
        return 0

    inserted = 0
    now = datetime.now(UTC)
    is_ch_connect = hasattr(ch, "query")

    # Check which sources already exist to skip dupes
    # Get all distinct source values from wallet_labels
    seen_sources: set[str] = set()
    try:
        result = _ch_select(ch, "SELECT DISTINCT source FROM wallet_labels")
        if result:
            seen_sources = {r[0] for r in result}
    except Exception:  # noqa: S110 — intentional best-effort
        pass

    for i in range(0, len(labels), batch_size):
        batch = labels[i : i + batch_size]
        ch_rows: list = []
        redis_pipe_ops: list[tuple[str, str]] = []

        for lbl in batch:
            addr = lbl["address"].lower().strip()
            chain = lbl.get("chain_id", "ethereum").lower().strip()
            name = lbl.get("label_name", "").strip()[:256]
            category = lbl.get("label_category", "").strip()[:128]
            subtype = lbl.get("label_subtype", "").strip()[:128]
            source = lbl.get("source", "ingest_script").strip()[:128]
            sanctioned = 1 if lbl.get("is_sanctioned", False) else 0

            if not addr or len(addr) < 10 or not name:
                continue

            # Skip if source was already ingested
            if source in seen_sources:
                continue

            row = [addr, chain, name, category, subtype, source, sanctioned, now]
            if not is_ch_connect:
                row = tuple(row)
            ch_rows.append(row)

            # Redis hot cache
            if redis:
                label_json = json.dumps(
                    {"chain": chain, "address": addr, "name": name, "type": category, "source": source}
                )
                redis_pipe_ops.append((f"rmi:label:{chain}:{addr}", label_json))

        if ch_rows:
            try:
                if is_ch_connect:
                    ch.insert(
                        "wallet_labels",
                        ch_rows,
                        column_names=[
                            "address",
                            "chain_id",
                            "label_name",
                            "label_category",
                            "label_subtype",
                            "source",
                            "is_sanctioned",
                            "loaded_at",
                        ],
                    )
                else:
                    ch.execute(
                        """INSERT INTO wallet_labels
                        (address, chain_id, label_name, label_category, label_subtype,
                         source, is_sanctioned, loaded_at) VALUES""",
                        ch_rows,
                    )
                inserted += len(ch_rows)
            except Exception as e:
                logger.warning(f"ClickHouse insert failed ({len(ch_rows)} rows): {e}")

        if redis and redis_pipe_ops:
            try:
                pipe = redis.pipeline()
                for rk, rv in redis_pipe_ops:
                    pipe.set(rk, rv)
                pipe.execute()
            except Exception as e:
                logger.debug(f"Redis pipeline failed: {e}")

        if (i // batch_size) % 10 == 0 and i > 0 and not (i // batch_size) % 100:
            logger.info(f"  Inserted {inserted}/{len(labels)} labels...")

    return inserted


def _batch_insert_scam_addresses(
    ch: Any,
    redis: Any | None,
    addresses: list[dict[str, Any]],
    batch_size: int = 500,
) -> int:
    """Batch insert into scam_addresses table."""
    if not addresses:
        return 0

    inserted = 0
    now = datetime.now(UTC)
    is_ch_connect = hasattr(ch, "query")

    for i in range(0, len(addresses), batch_size):
        batch = addresses[i : i + batch_size]
        rows: list = []

        for entry in batch:
            addr = entry["address"].lower().strip()
            chain = entry.get("chain_id", "ethereum").lower().strip()
            source = entry.get("source", "ingest_script").strip()[:128]
            threat = entry.get("threat_type", "scam").strip()[:128]
            confidence = float(entry.get("confidence", 0.9))
            evidence = entry.get("evidence", "").strip()[:1024]

            if not addr or len(addr) < 10:
                continue

            row = [addr, chain, source, threat, confidence, now, evidence]
            if not is_ch_connect:
                row = tuple(row)
            rows.append(row)

        if rows:
            try:
                if is_ch_connect:
                    ch.insert(
                        "scam_addresses",
                        rows,
                        column_names=[
                            "address",
                            "chain_id",
                            "source",
                            "threat_type",
                            "confidence",
                            "first_seen_at",
                            "evidence",
                        ],
                    )
                else:
                    ch.execute(
                        """INSERT INTO scam_addresses
                        (address, chain_id, source, threat_type, confidence, first_seen_at, evidence)
                        VALUES""",
                        rows,
                    )
                inserted += len(rows)
            except Exception as e:
                logger.warning(f"ClickHouse scam insert failed: {e}")

            # Redis hot cache
            if redis:
                try:
                    pipe = redis.pipeline()
                    for r in rows:
                        cache_key = f"wm:scam:{r[1]}:{r[0]}"
                        data = {"source": r[2], "threat_type": r[3], "confidence": r[4]}
                        pipe.set(cache_key, json.dumps(data))
                        pipe.sadd(f"wm:scam_set:{r[1]}", r[0])
                    pipe.execute()
                except Exception as e:
                    logger.debug(f"Redis scam cache failed: {e}")

    return inserted


# ===========================================================================
# Source 1: Built-in Contract Directories
# ===========================================================================


async def ingest_builtin_contracts(ch: Any, redis: Any | None, chain_filter: str = "") -> dict[str, int]:
    """Ingest known contract addresses from built-in directories.

    Sources: Tornado Cash, Uniswap, 1inch, DeFi admin, Gnosis Safe
    """
    logger.info("=" * 60)
    logger.info("SOURCE: Built-in Contract Directories")
    logger.info("=" * 60)

    totals = {"wallet_labels": 0, "scam_addresses": 0}
    combined_sources: dict[str, list[dict[str, str]]] = {}

    # Merge all built-in directories
    for name, dirs in [
        ("tornado_cash", TORNADO_CASH_ADDRESSES),
        ("uniswap_routers", UNISWAP_ROUTERS),
        ("1inch_routers", INCH_ROUTERS),
        ("defi_admin", DEFI_ADMIN_ADDRESSES),
        ("gnosis_safe", GNOSIS_SAFE_ADDRESSES),
    ]:
        for chain, entries in dirs.items():
            if chain_filter and chain != chain_filter:
                continue
            for entry in entries:
                combined_sources.setdefault(chain, []).append(
                    {
                        **entry,
                        "source_name": name,
                    }
                )

    # Build wallet_labels rows
    label_rows = []
    scam_rows = []

    for chain, entries in combined_sources.items():
        for entry in entries:
            # Wallet labels
            label_rows.append(
                {
                    "address": entry["address"],
                    "chain_id": chain,
                    "label_name": entry["name"],
                    "label_category": entry["category"],
                    "label_subtype": "builtin",
                    "source": f"builtin:{entry['source_name']}",
                    "is_sanctioned": False,
                }
            )

            # Mixers go to scam_addresses too
            if entry["category"] == "mixer":
                scam_rows.append(
                    {
                        "address": entry["address"],
                        "chain_id": chain,
                        "source": f"builtin:{entry['source_name']}",
                        "threat_type": "mixer",
                        "confidence": 1.0,
                        "evidence": f"Known mixer: {entry['name']}",
                    }
                )

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt
        logger.info(f"  → {cnt} wallet labels from built-in directories")

    if scam_rows:
        cnt = _batch_insert_scam_addresses(ch, redis, scam_rows)
        totals["scam_addresses"] = cnt
        logger.info(f"  → {cnt} scam addresses (mixers) from built-in directories")

    return totals


# ===========================================================================
# Source 2: Etherscan Labels API
# ===========================================================================


async def ingest_etherscan_labels(
    ch: Any,
    redis: Any | None,
    chain: str = "ethereum",
    addresses: list[str] | None = None,
) -> dict[str, int]:
    """Fetch labels from Etherscan API for known scam directories.

    If `addresses` provided, queries those specific addresses.
    Otherwise fetches from local CSVs (etherscan_phish_hack, etherscan_combined_labels).
    """
    logger.info("=" * 60)
    logger.info(f"SOURCE: Etherscan Labels ({chain})")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")

    # ── Try loading from CSVs first ──
    label_rows = []
    scam_rows = []

    # etherscan_phish_hack.csv - these are confirmed scam/phishing
    phish_path = os.path.join(data_dir, "etherscan_phish_hack.csv")
    if os.path.exists(phish_path):
        try:
            with open(phish_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    addr = row.get("address", "").strip()
                    name_tag = row.get("name_tag", "").strip()
                    label_type = row.get("label_type", "phish-hack").strip()
                    row_chain = row.get("chain", "Ethereum").strip()
                    if chain != "all" and chain.lower() not in row_chain.lower():
                        continue
                    if addr and len(addr) >= 10:
                        label_rows.append(
                            {
                                "address": addr.lower(),
                                "chain_id": "ethereum",
                                "label_name": name_tag or f"Etherscan {label_type}",
                                "label_category": "scam",
                                "label_subtype": label_type,
                                "source": "etherscan_phish_hack",
                                "is_sanctioned": False,
                            }
                        )
                        scam_rows.append(
                            {
                                "address": addr.lower(),
                                "chain_id": "ethereum",
                                "source": "etherscan_phish_hack",
                                "threat_type": label_type,
                                "confidence": 0.95,
                                "evidence": f"Etherscan label: {name_tag} ({label_type})",
                            }
                        )
        except Exception as e:
            logger.warning(f"Failed to read phish CSV: {e}")

        logger.info(f"  Read {len(label_rows)} rows from etherscan_phish_hack.csv")

    # etherscan_combined_labels.csv - all labeled addresses
    combined_path = os.path.join(data_dir, "etherscan_combined_labels.csv")
    combined_scam_keywords = [
        "phish",
        "hack",
        "exploit",
        "scam",
        "drain",
        "attack",
        "heist",
        "malicious",
        "fraud",
        "rug",
        "compromis",
    ]

    if os.path.exists(combined_path):
        combined_labels = []
        combined_scams = []
        try:
            with open(combined_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    addr = row.get("address", "").strip()
                    label = row.get("label", "").strip()
                    name = row.get("name", "").strip()
                    row_chain = row.get("chain", "ethereum").strip().lower()
                    if chain != "all" and chain.lower() not in row_chain:
                        continue
                    if not addr or len(addr) < 10:
                        continue

                    # Detect if scam label
                    is_scam = any(kw in label.lower() for kw in combined_scam_keywords)

                    category = "scam" if is_scam else "entity"
                    combined_labels.append(
                        {
                            "address": addr.lower(),
                            "chain_id": row_chain,
                            "label_name": name or label,
                            "label_category": category,
                            "label_subtype": label,
                            "source": "etherscan_combined",
                            "is_sanctioned": False,
                        }
                    )

                    if is_scam:
                        combined_scams.append(
                            {
                                "address": addr.lower(),
                                "chain_id": row_chain,
                                "source": "etherscan_combined",
                                "threat_type": label,
                                "confidence": 0.9,
                                "evidence": f"Etherscan combined label: {label}",
                            }
                        )
        except Exception as e:
            logger.warning(f"Failed to read combined CSV: {e}")

        if combined_labels:
            cnt = _batch_insert_wallet_labels(ch, redis, combined_labels)
            totals["wallet_labels"] += cnt
            logger.info(f"  → {cnt} wallet labels from etherscan_combined_labels.csv")

        if combined_scams:
            cnt = _batch_insert_scam_addresses(ch, redis, combined_scams)
            totals["scam_addresses"] += cnt
            logger.info(f"  → {cnt} scam addresses from etherscan_combined_labels.csv")

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] += cnt
        if scam_rows:
            cnt = _batch_insert_scam_addresses(ch, redis, scam_rows)
            totals["scam_addresses"] += cnt

    return totals


# ===========================================================================
# Source 3: CryptoScamDB
# ===========================================================================


async def ingest_cryptoscamdb(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch scam addresses from CryptoScamDB API."""
    logger.info("=" * 60)
    logger.info("SOURCE: CryptoScamDB")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}

    label_rows: list[dict] = []
    scam_rows: list[dict] = []

    # Primary: GitHub urls.yaml (master). The public API is intermittently 502
    # and data/blacklist.json never existed upstream (old URL 404'd).
    github_url = "https://raw.githubusercontent.com/CryptoScamDB/blacklist/master/data/urls.yaml"
    chain_alias = {"eth": "ethereum", "btc": "bitcoin", "bnb": "bsc"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(github_url)
            if resp.status_code == 200:
                if yaml is None:
                    logger.error("  pyyaml not installed — cannot parse CryptoScamDB YAML")
                else:
                    entries = yaml.safe_load(resp.text)
                    if isinstance(entries, list):
                        logger.info(f"  Got {len(entries)} entries from GitHub urls.yaml")
                        for entry in entries:
                            if not isinstance(entry, dict):
                                continue
                            name = entry.get("name", "")
                            category = entry.get("category", "scam")
                            addresses = entry.get("addresses", {})
                            if not isinstance(addresses, dict):
                                continue
                            for chain, chain_addrs in addresses.items():
                                if not isinstance(chain_addrs, list):
                                    continue
                                for addr in chain_addrs:
                                    if not addr or len(addr) < 10:
                                        continue
                                    label_rows.append(
                                        {
                                            "address": addr.lower(),
                                            "chain_id": chain_alias.get(chain.lower(), chain.lower()),
                                            "label_name": name or f"CryptoScamDB {category}",
                                            "label_category": "scam",
                                            "label_subtype": category,
                                            "source": "cryptoscamdb",
                                            "is_sanctioned": False,
                                        }
                                    )
                                    scam_rows.append(
                                        {
                                            "address": addr.lower(),
                                            "chain_id": chain_alias.get(chain.lower(), chain.lower()),
                                            "source": "cryptoscamdb",
                                            "threat_type": category,
                                            "confidence": 0.9,
                                            "evidence": f"CryptoScamDB: {name}",
                                        }
                                    )
            else:
                logger.warning(f"  CryptoScamDB GitHub returned {resp.status_code}")
    except Exception as e:
        logger.warning(f"  CryptoScamDB GitHub fetch failed: {e}")

    # Fallback: API (intermittently 502)
    api_urls = [
        "https://api.cryptoscamdb.org/v1/scams",
        "https://api.cryptoscamdb.org/v1/all",
    ]

    data = None
    if not label_rows:
        async with httpx.AsyncClient(timeout=30) as client:
            for url in api_urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        logger.info(f"  Got response from {url}")
                        break
                    else:
                        logger.warning(f"  {url} returned {resp.status_code}")
                except Exception as e:
                    logger.debug(f"  {url} failed: {e}")

    if not data and not label_rows:
        logger.warning("  CryptoScamDB unavailable (API 502 + GitHub fallback)")
        return totals

    # Parse response
    scams = []
    if isinstance(data, dict):
        scams = data.get("result", data.get("scams", data.get("data", [])))
    elif isinstance(data, list):
        scams = data

    logger.info(f"  Found {len(scams)} scam entries")

    for scam in scams:
        if not isinstance(scam, dict):
            continue

        name = scam.get("name", "")
        category = scam.get("category", "scam")
        status = scam.get("status", "unknown")
        description = scam.get("description", "")
        addresses = scam.get("addresses", [])

        # Chain inference from category
        chain = "ethereum"
        cat_lower = category.lower()
        if "bitcoin" in cat_lower or "btc" in cat_lower:
            chain = "bitcoin"
        elif "solana" in cat_lower or "sol" in cat_lower:
            chain = "solana"
        elif "bsc" in cat_lower or "bnb" in cat_lower:
            chain = "bsc"

        if addresses:
            for addr in addresses:
                if not addr or len(addr) < 10:
                    continue
                label_rows.append(
                    {
                        "address": addr.lower(),
                        "chain_id": chain,
                        "label_name": name or f"CryptoScamDB {category}",
                        "label_category": "scam",
                        "label_subtype": category,
                        "source": "cryptoscamdb",
                        "is_sanctioned": False,
                    }
                )
                scam_rows.append(
                    {
                        "address": addr.lower(),
                        "chain_id": chain,
                        "source": "cryptoscamdb",
                        "threat_type": category,
                        "confidence": 0.9 if status == "Active" else 0.7,
                        "evidence": f"CryptoScamDB: {name} | {description[:200]}"
                        if description
                        else f"CryptoScamDB: {name}",
                    }
                )

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt
    if scam_rows:
        cnt = _batch_insert_scam_addresses(ch, redis, scam_rows)
        totals["scam_addresses"] = cnt

    logger.info(f"  → {len(label_rows)} wallet labels, {len(scam_rows)} scam addresses from CryptoScamDB")
    return totals


# ===========================================================================
# Source 4: ChainAbuse API
# ===========================================================================


async def ingest_chainabuse(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch scam reports from ChainAbuse API."""
    logger.info("=" * 60)
    logger.info("SOURCE: ChainAbuse")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}

    api_url = "https://api.chainabuse.com/v1/reports"
    alt_urls = [
        "https://api.chainabuse.com/v0/reports",
        "https://chainabuse.com/api/reports",
    ]

    all_reports = []

    async with httpx.AsyncClient(timeout=30) as client:
        # Try primary endpoint with pagination
        for page in range(3):
            url = f"{api_url}?limit=100&offset={page * 100}"
            try:
                resp = await client.get(url, headers={"Accept": "application/json"})
                if resp.status_code == 200:
                    data = resp.json()
                    reports = data if isinstance(data, list) else data.get("data", data.get("reports", []))
                    if reports:
                        all_reports.extend(reports)
                        logger.info(f"  Page {page + 1}: {len(reports)} reports (total: {len(all_reports)})")
                    if len(reports) < 100:
                        break
                elif resp.status_code == 401:
                    logger.warning("  ChainAbuse requires auth - trying alt endpoints")
                    break
                else:
                    logger.warning(f"  ChainAbuse returned {resp.status_code}")
                    break
            except Exception as e:
                logger.debug(f"  ChainAbuse page {page} failed: {e}")
                break

        # Try alt endpoints if primary failed
        if not all_reports:
            for alt_url in alt_urls:
                try:
                    resp = await client.get(alt_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        reports = data if isinstance(data, list) else data.get("data", data.get("reports", []))
                        if reports:
                            all_reports.extend(reports)
                            logger.info(f"  Got {len(reports)} from alt endpoint: {alt_url}")
                            break
                except Exception:  # noqa: S110 — intentional best-effort
                    pass

    if not all_reports:
        logger.warning("  No ChainAbuse reports accessible")
        return totals

    logger.info(f"  Processing {len(all_reports)} reports")

    label_rows = []
    scam_rows = []

    for report in all_reports:
        if not isinstance(report, dict):
            continue

        address = report.get("address", report.get("targetAddress", "")).strip()
        category = report.get("category", report.get("type", "scam"))
        description = report.get("description", report.get("report", ""))
        chain = report.get("chain", report.get("network", "ethereum")).lower()

        if not address or len(address) < 10:
            continue

        label_rows.append(
            {
                "address": address.lower(),
                "chain_id": chain
                if chain in ("ethereum", "solana", "bsc", "bitcoin", "polygon", "arbitrum", "base")
                else "ethereum",
                "label_name": f"ChainAbuse: {category}",
                "label_category": "scam",
                "label_subtype": category,
                "source": "chainabuse",
                "is_sanctioned": False,
            }
        )
        scam_rows.append(
            {
                "address": address.lower(),
                "chain_id": chain
                if chain in ("ethereum", "solana", "bsc", "bitcoin", "polygon", "arbitrum", "base")
                else "ethereum",
                "source": "chainabuse",
                "threat_type": category,
                "confidence": 0.85,
                "evidence": f"ChainAbuse: {description[:200]}" if description else f"ChainAbuse category: {category}",
            }
        )

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt
    if scam_rows:
        cnt = _batch_insert_scam_addresses(ch, redis, scam_rows)
        totals["scam_addresses"] = cnt

    logger.info(f"  → {len(label_rows)} wallet labels, {len(scam_rows)} scam addresses from ChainAbuse")
    return totals


# ===========================================================================
# Source 5: RolodETH (GitHub label database)
# ===========================================================================


async def ingest_rolodeth(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch address labels from RolodETH GitHub repository."""
    logger.info("=" * 60)
    logger.info("SOURCE: RolodETH")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}

    urls = [
        "https://raw.githubusercontent.com/RoleTiker/rolodETH/main/rolodETH.json",
        "https://raw.githubusercontent.com/exdx/rolodETH/main/data/labels.json",
    ]

    data = None
    async with httpx.AsyncClient(timeout=30) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"  Got data from {url.split('/')[-1]}")
                    break
            except Exception as e:
                logger.debug(f"  RolodETH {url} failed: {e}")

    if not data:
        logger.warning("  RolodETH unavailable")
        return totals

    label_rows = []

    # Parse various formats
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                addr = entry.get("address", "").strip()
                label = entry.get("label", entry.get("name", "")).strip()
                category = entry.get("category", entry.get("type", "wallet")).strip()
                if addr and len(addr) >= 10 and label:
                    label_rows.append(
                        {
                            "address": addr.lower(),
                            "chain_id": "ethereum",
                            "label_name": label,
                            "label_category": _infer_label_category(category),
                            "label_subtype": category,
                            "source": "rolodeth",
                            "is_sanctioned": False,
                        }
                    )
    elif isinstance(data, dict):
        for addr, info in data.items():
            addr_norm = addr.strip().lower()
            if not addr_norm or len(addr_norm) < 10:
                continue
            if isinstance(info, str):
                label_rows.append(
                    {
                        "address": addr_norm,
                        "chain_id": "ethereum",
                        "label_name": info.strip()[:256],
                        "label_category": "wallet",
                        "label_subtype": "",
                        "source": "rolodeth",
                        "is_sanctioned": False,
                    }
                )
            elif isinstance(info, dict):
                label = info.get("label", info.get("name", "")).strip()
                category = info.get("category", info.get("type", "wallet")).strip()
                if label:
                    label_rows.append(
                        {
                            "address": addr_norm,
                            "chain_id": "ethereum",
                            "label_name": label,
                            "label_category": _infer_label_category(category),
                            "label_subtype": category,
                            "source": "rolodeth",
                            "is_sanctioned": False,
                        }
                    )
            elif isinstance(info, list):
                for sub in info:
                    if isinstance(sub, dict):
                        label = sub.get("label", sub.get("name", "")).strip()
                        category = sub.get("category", sub.get("type", "wallet")).strip()
                        if label:
                            label_rows.append(
                                {
                                    "address": addr_norm,
                                    "chain_id": "ethereum",
                                    "label_name": label,
                                    "label_category": _infer_label_category(category),
                                    "label_subtype": category,
                                    "source": "rolodeth",
                                    "is_sanctioned": False,
                                }
                            )

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt

    logger.info(f"  → {len(label_rows)} wallet labels from RolodETH")
    return totals


# ===========================================================================
# Source 6: walletLabels.xyz (batch fetch by contract list)
# ===========================================================================


async def ingest_walletlabels_xyz(ch: Any, redis: Any | None, chain: str = "ethereum") -> dict[str, int]:
    """Fetch labels from walletLabels.xyz API for known addresses.

    This is a live per-address API, so we query a batch of known
    scam/defi/exchange addresses to enrich our database.
    """
    logger.info("=" * 60)
    logger.info(f"SOURCE: walletLabels.xyz ({chain})")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}

    # Pull some high-value addresses from our existing data to enrich
    addresses_to_check: list[str] = []

    try:
        if ch:
            rows = _ch_select(
                ch,
                "SELECT DISTINCT address FROM wallet_labels WHERE chain_id = {chain:String} LIMIT 200",
                {"chain": chain},
            )
            addresses_to_check = [r[0] for r in rows[:50]]  # Batch of 50
    except Exception:  # noqa: S110 — intentional best-effort
        pass

    if not addresses_to_check:
        # Fallback: well-known addresses
        addresses_to_check = [
            addr for entries in TORNADO_CASH_ADDRESSES.get(chain, []) for addr in [entries["address"]]
        ]
        for entries in UNISWAP_ROUTERS.get(chain, []):
            addresses_to_check.append(entries["address"])

    logger.info(f"  Checking {len(addresses_to_check)} addresses")

    label_rows = []
    seen = set()

    async with httpx.AsyncClient(timeout=15) as client:
        for addr in addresses_to_check:
            try:
                resp = await client.get(f"https://api.walletlabels.xyz/v1/address/{addr}", timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("labels", data.get("data", []))
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                label = item.get("label", item.get("name", "")).strip()
                                category = item.get("category", item.get("type", "")).strip()
                                dedup_key = f"{addr.lower()}:{label}"
                                if label and dedup_key not in seen:
                                    seen.add(dedup_key)
                                    label_rows.append(
                                        {
                                            "address": addr.lower(),
                                            "chain_id": chain,
                                            "label_name": label,
                                            "label_category": _infer_label_category(category),
                                            "label_subtype": category,
                                            "source": "walletlabels_xyz",
                                            "is_sanctioned": False,
                                        }
                                    )
                    elif isinstance(items, dict):
                        label = items.get("label", items.get("name", "")).strip()
                        category = items.get("category", "").strip()
                        dedup_key = f"{addr.lower()}:{label}"
                        if label and dedup_key not in seen:
                            seen.add(dedup_key)
                            label_rows.append(
                                {
                                    "address": addr.lower(),
                                    "chain_id": chain,
                                    "label_name": label,
                                    "label_category": _infer_label_category(category),
                                    "label_subtype": category,
                                    "source": "walletlabels_xyz",
                                    "is_sanctioned": False,
                                }
                            )
            except Exception:  # noqa: S110 — intentional best-effort
                pass

            await asyncio.sleep(0.05)  # Rate limit

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt

    logger.info(f"  → {len(label_rows)} wallet labels from walletLabels.xyz")
    return totals


# ===========================================================================
# Source 7: bluepages.fyi
# ===========================================================================


async def ingest_bluepages(ch: Any, redis: Any | None, chain: str = "ethereum") -> dict[str, int]:
    """Fetch labels from bluepages.fyi API."""
    logger.info("=" * 60)
    logger.info(f"SOURCE: bluepages.fyi ({chain})")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}

    # Same address list as walletLabels
    addresses_to_check: list[str] = []
    for entries in TORNADO_CASH_ADDRESSES.get(chain, []):
        addresses_to_check.append(entries["address"])
    for entries in UNISWAP_ROUTERS.get(chain, []):
        addresses_to_check.append(entries["address"])
    for entries in INCH_ROUTERS.get(chain, []):
        addresses_to_check.append(entries["address"])
    for entries in DEFI_ADMIN_ADDRESSES.get(chain, []):
        addresses_to_check.append(entries["address"])

    # Deduplicate
    addresses_to_check = list(dict.fromkeys(addresses_to_check))

    logger.info(f"  Checking {len(addresses_to_check)} addresses")

    label_rows = []
    seen = set()

    async with httpx.AsyncClient(timeout=15) as client:
        for addr in addresses_to_check:
            try:
                resp = await client.get(f"https://bluepages.fyi/api/v1/address/{addr}", timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if isinstance(item, dict):
                            label = item.get("label", item.get("name", "")).strip()
                            category = item.get("category", item.get("type", "")).strip()
                            dedup_key = f"{addr.lower()}:{label}"
                            if label and dedup_key not in seen:
                                seen.add(dedup_key)
                                label_rows.append(
                                    {
                                        "address": addr.lower(),
                                        "chain_id": chain,
                                        "label_name": label,
                                        "label_category": _infer_label_category(category),
                                        "label_subtype": category,
                                        "source": "bluepages_fyi",
                                        "is_sanctioned": False,
                                    }
                                )
            except Exception:  # noqa: S110 — intentional best-effort
                pass

            await asyncio.sleep(0.05)

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt

    logger.info(f"  → {len(label_rows)} wallet labels from bluepages.fyi")
    return totals


# ===========================================================================
# Source 8: MetaMask eth-phishing-detect (addresses only, not domains)
# ===========================================================================


async def ingest_metamask_phishing(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch address-level phishing data from MetaMask phishing detect.

    Note: MetaMask list is primarily domains, but we extract any addresses
    that appear in the blacklist config.
    """
    logger.info("=" * 60)
    logger.info("SOURCE: MetaMask Phishing Detect")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}

    url = "https://raw.githubusercontent.com/MetaMask/eth-phishing-detect/master/src/config.json"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(f"  MetaMask list returned {resp.status_code}")
                return totals
            data = resp.json()
        except Exception as e:
            logger.warning(f"  MetaMask fetch failed: {e}")
            return totals

    # The config contains:
    # - blacklist: domain patterns
    # - fuzzylist: fuzzy domain matches
    # - tolerance: matching tolerance
    # We extract any addresses embedded in the config
    address_patterns = set()

    def _extract_addresses(obj: Any) -> None:
        """Recursively extract hex addresses from config."""
        if isinstance(obj, str) and obj.startswith("0x") and len(obj) == 42:
            address_patterns.add(obj.lower())
        elif isinstance(obj, dict):
            for val in obj.values():
                _extract_addresses(val)
        elif isinstance(obj, list):
            for item in obj:
                _extract_addresses(item)

    _extract_addresses(data)

    if not address_patterns:
        logger.info("  No address patterns found in MetaMask config (domain-only list)")
        return totals

    label_rows = []
    scam_rows = []

    for addr in address_patterns:
        label_rows.append(
            {
                "address": addr,
                "chain_id": "ethereum",
                "label_name": "MetaMask Phishing",
                "label_category": "phishing",
                "label_subtype": "metamask_phishing_detect",
                "source": "metamask_phishing_detect",
                "is_sanctioned": False,
            }
        )
        scam_rows.append(
            {
                "address": addr,
                "chain_id": "ethereum",
                "source": "metamask_phishing_detect",
                "threat_type": "phishing",
                "confidence": 0.95,
                "evidence": "MetaMask eth-phishing-detect confirmed phishing address",
            }
        )

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt
    if scam_rows:
        cnt = _batch_insert_scam_addresses(ch, redis, scam_rows)
        totals["scam_addresses"] = cnt

    logger.info(f"  → {len(label_rows)} labels, {len(scam_rows)} scam from MetaMask phishing detect")
    return totals


# ===========================================================================
# Source 9: Open Labels Initiative (OLI) - public endpoint
# ===========================================================================


async def ingest_open_labels(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch from Open Labels Initiative if endpoint available."""
    logger.info("=" * 60)
    logger.info("SOURCE: Open Labels Initiative")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}

    # OLI public endpoint - may not exist yet (placeholder)
    urls = [
        "https://raw.githubusercontent.com/openlabelsinitiative/OLI/main/data/labels.json",
        "https://openlabels.xyz/api/v1/labels",
    ]

    data = None
    async with httpx.AsyncClient(timeout=15) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"  Got data from {url}")
                    break
            except Exception:  # noqa: S110 — best-effort rate limiting
                pass

    if not data:
        logger.info("  OLI endpoint unavailable (expected - still in development)")
        return totals

    label_rows = []

    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                addr = entry.get("address", entry.get("id", "")).strip()
                label = entry.get("label", entry.get("name", "")).strip()
                chain = entry.get("chain", "ethereum").strip().lower()
                category = entry.get("category", "wallet").strip()
                if addr and len(addr) >= 10 and label:
                    label_rows.append(
                        {
                            "address": addr.lower(),
                            "chain_id": chain,
                            "label_name": label,
                            "label_category": _infer_label_category(category),
                            "label_subtype": category,
                            "source": "open_labels_initiative",
                            "is_sanctioned": False,
                        }
                    )

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt

    logger.info(f"  → {len(label_rows)} wallet labels from OLI")
    return totals


# ===========================================================================
# Source 10: Internal Scanner Findings (scam_addresses → wallet_labels)
# ===========================================================================


async def ingest_scanner_findings(ch: Any, redis: Any | None) -> dict[str, int]:
    """Read existing scam_addresses (from scanner auto-labeling) and
    ensure they also have corresponding wallet_labels entries.

    This closes the loop: scanner flags a deployer → scam_addresses entry
    is created → this sync creates the wallet_labels entry.
    """
    logger.info("=" * 60)
    logger.info("SOURCE: Internal Scanner Findings")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0}

    if not ch:
        logger.warning("  ClickHouse unavailable - cannot read scanner findings")
        return totals

    try:
        rows = _ch_select(
            ch,
            """SELECT address, chain_id, source, threat_type, confidence, evidence
               FROM scam_addresses
               WHERE threat_type IN ('scam', 'malicious', 'exploiter', 'phishing',
                                     'money_laundering', 'ponzi', 'high_risk_deployer',
                                     'malicious_deployer', 'rug', 'hack')
               ORDER BY first_seen_at DESC
               LIMIT 10000""",
        )
    except Exception as e:
        logger.warning(f"  Failed to query scam_addresses: {e}")
        return totals

    if not rows:
        logger.info("  No scanner findings to sync")
        return totals

    logger.info(f"  Found {len(rows)} scanner findings to sync")

    label_rows = []
    seen = set()

    for row in rows:
        addr = row[0]
        chain = row[1]
        source = row[2]
        threat = row[3]
        evidence = row[5] if len(row) > 5 else ""

        dedup_key = f"{addr}:{chain}:scanner_findings"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        label_rows.append(
            {
                "address": addr,
                "chain_id": chain,
                "label_name": f"{source}: {threat}",
                "label_category": "scam",
                "label_subtype": threat,
                "source": f"scanner:{source}",
                "is_sanctioned": False,
            }
        )

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt

    logger.info(f"  → {len(label_rows)} wallet labels synced from scanner findings")
    return totals


# ===========================================================================
# Source 11: GitHub community label CSVs (bsuberca, etc.)
# ===========================================================================


async def ingest_github_community(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch community-maintained address label CSVs from GitHub."""
    logger.info("=" * 60)
    logger.info("SOURCE: GitHub Community Labels")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}

    # Community label sources (public CSVs/JSONs on GitHub)
    community_sources = [
        {
            "url": "https://raw.githubusercontent.com/ImMike/crypto-wallet-address-labels/main/datasets/ethereum-exchange-and-dex-labels/exchange.csv",
            "chain": "ethereum",
            "category": "exchange",
        },
        {
            "url": "https://raw.githubusercontent.com/ImMike/crypto-wallet-address-labels/main/datasets/ethereum-exchange-and-dex-labels/dex.csv",
            "chain": "ethereum",
            "category": "dex",
        },
        {
            "url": "https://raw.githubusercontent.com/ImMike/crypto-wallet-address-labels/main/datasets/ethereum-cluster-exchange-labels/cluster1.csv",
            "chain": "ethereum",
            "category": "exchange",
        },
        {
            "url": "https://raw.githubusercontent.com/ImMike/crypto-wallet-address-labels/main/datasets/bsc-ethereum-address-tags/bsc_detail.csv",
            "chain": "bsc",
            "category": "entity",
        },
        {
            "url": "https://raw.githubusercontent.com/bitcoin-scammers-list/bitcoin-scammers-list/main/scammers.csv",
            "chain": "bitcoin",
            "category": "scam",
        },
    ]

    label_rows = []
    scam_rows = []

    async with httpx.AsyncClient(timeout=30) as client:
        for source in community_sources:
            url = source["url"]
            chain = source["chain"]
            category = source["category"]
            source_name = url.split("/")[-1].replace(".csv", "")

            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug(f"  {source_name} returned {resp.status_code}")
                    continue
                content = resp.text
            except Exception as e:
                logger.debug(f"  {source_name} failed: {e}")
                continue

            # Parse CSV from response text
            reader = csv.DictReader(content.splitlines())
            count = 0
            for row in reader:
                # Try common column names
                addr = row.get("address", row.get("Address", row.get("addr", row.get("wallet", "")))).strip()
                name = row.get("name", row.get("Name", row.get("label", row.get("Label", "")))).strip()
                if not addr or len(addr) < 10:
                    continue
                if not name:
                    name = source_name

                label_rows.append(
                    {
                        "address": addr.lower(),
                        "chain_id": chain,
                        "label_name": name,
                        "label_category": category,
                        "label_subtype": "community",
                        "source": f"github:{source_name}",
                        "is_sanctioned": False,
                    }
                )

                if category == "scam":
                    scam_rows.append(
                        {
                            "address": addr.lower(),
                            "chain_id": chain,
                            "source": f"github:{source_name}",
                            "threat_type": "scam",
                            "confidence": 0.8,
                            "evidence": f"Community list: {name}",
                        }
                    )

                count += 1

            logger.info(f"  {source_name}: {count} rows")

    if label_rows:
        cnt = _batch_insert_wallet_labels(ch, redis, label_rows)
        totals["wallet_labels"] = cnt
    if scam_rows:
        cnt = _batch_insert_scam_addresses(ch, redis, scam_rows)
        totals["scam_addresses"] = cnt

    logger.info(f"  → {len(label_rows)} wallet labels, {len(scam_rows)} scam from GitHub community")
    return totals


# ===========================================================================
# Helpers
# ===========================================================================


def _infer_label_category(text: str) -> str:
    """Infer label type category from text."""
    if not text:
        return "wallet"
    t = text.lower().strip()
    if any(w in t for w in ("exchange", "cex", "binance", "coinbase", "kraken", "okx")):
        return "exchange"
    if any(w in t for w in ("scam", "phish", "hack", "exploit", "fraud", "rug", "drainer", "malicious")):
        return "scam"
    if any(w in t for w in ("mixer", "tornado", "wasabi", "coinjoin")):
        return "mixer"
    if any(w in t for w in ("sanction", "ofac")):
        return "sanctioned"
    if any(w in t for w in ("defi", "aave", "uniswap", "curve", "compound", "lido", "maker")):
        return "defi"
    if any(w in t for w in ("dex", "swap", "lp", "liquidity")):
        return "dex"
    if any(w in t for w in ("bridge", "hop", "across", "synapse", "stargate", "wormhole")):
        return "bridge"
    if any(w in t for w in ("mev", "bot", "flashbot", "sandwich", "searcher")):
        return "mev"
    if any(w in t for w in ("multisig", "gnosis", "safe")):
        return "multisig"
    if any(w in t for w in ("admin", "timelock", "governance", "treasury")):
        return "defi_admin"
    if any(w in t for w in ("mining", "pool", "miner")):
        return "mining_pool"
    if any(w in t for w in ("gambling", "casino", "bet")):
        return "gambling"
    if any(w in t for w in ("ransomware", "ransom")):
        return "ransomware"
    if any(w in t for w in ("ponzi", "pyramid")):
        return "ponzi"
    return "entity"


# ===========================================================================
# Report: Show current label distribution
# ===========================================================================


async def show_report(ch: Any) -> None:
    """Display current wallet label statistics."""
    if not ch:
        print("ClickHouse unavailable - cannot show report")
        return

    print("\n" + "=" * 70)
    print("WALLET LABEL INVENTORY REPORT")
    print("=" * 70)

    try:
        # Total wallet_labels
        result = _ch_select(ch, "SELECT count() FROM wallet_labels")
        total = result[0][0] if result else 0
        print(f"\n📊 wallet_labels total: {total:,}")

        # By label_category
        rows = _ch_select(
            ch,
            """SELECT label_category, count() as cnt
               FROM wallet_labels
               GROUP BY label_category
               ORDER BY cnt DESC
               LIMIT 20""",
        )
        print("\n── By Category ──")
        for row in rows or []:
            print(f"  {row[0]!s:<25s} {row[1]:>10,}")

        # By source
        rows = _ch_select(
            ch,
            """SELECT source, count() as cnt
               FROM wallet_labels
               GROUP BY source
               ORDER BY cnt DESC
               LIMIT 15""",
        )
        print("\n── By Source (top 15) ──")
        for row in rows or []:
            print(f"  {row[0]!s:<40s} {row[1]:>10,}")

        # Chain distribution
        rows = _ch_select(
            ch,
            """SELECT chain_id, count() as cnt
               FROM wallet_labels
               GROUP BY chain_id
               ORDER BY cnt DESC""",
        )
        print("\n── By Chain ──")
        for row in rows or []:
            print(f"  {row[0]!s:<15s} {row[1]:>10,}")

        # scam_addresses
        result = _ch_select(ch, "SELECT count() FROM scam_addresses")
        scam_total = result[0][0] if result else 0
        print(f"\n📊 scam_addresses total: {scam_total:,}")

        rows = _ch_select(
            ch,
            """SELECT threat_type, count() as cnt
               FROM scam_addresses
               GROUP BY threat_type
               ORDER BY cnt DESC
               LIMIT 10""",
        )
        print("\n── By Threat Type ──")
        for row in rows or []:
            print(f"  {row[0]!s:<25s} {row[1]:>10,}")

        rows = _ch_select(
            ch,
            """SELECT source, count() as cnt
               FROM scam_addresses
               GROUP BY source
               ORDER BY cnt DESC
               LIMIT 10""",
        )
        print("\n── By Source ──")
        for row in rows or []:
            print(f"  {row[0]!s:<40s} {row[1]:>10,}")

        # Recent scam address additions (last 7 days)
        result = _ch_select(
            ch,
            """SELECT count()
               FROM scam_addresses
               WHERE first_seen_at >= now() - INTERVAL 7 DAY""",
        )
        recent = result[0][0] if result else 0
        print(f"\n📈 scam_addresses added in last 7 days: {recent:,}")

        # Recent wallet_labels additions
        result = _ch_select(
            ch,
            """SELECT count()
               FROM wallet_labels
               WHERE loaded_at >= now() - INTERVAL 7 DAY""",
        )
        recent_labels = result[0][0] if result else 0
        print(f"📈 wallet_labels added in last 7 days: {recent_labels:,}")

    except Exception as e:
        print(f"Report generation failed: {e}")

    print("=" * 70)


# ===========================================================================
# Main CLI
# ===========================================================================


SOURCE_REGISTRY = {
    "builtin_contracts": ingest_builtin_contracts,
    "etherscan_labels": ingest_etherscan_labels,
    "cryptoscamdb": ingest_cryptoscamdb,
    "chainabuse": ingest_chainabuse,
    "rolodeth": ingest_rolodeth,
    "walletlabels_xyz": ingest_walletlabels_xyz,
    "bluepages": ingest_bluepages,
    "metamask_phishing": ingest_metamask_phishing,
    "open_labels": ingest_open_labels,
    "scanner_findings": ingest_scanner_findings,
    "github_community": ingest_github_community,
}


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest wallet labels from public directories into ClickHouse + Redis",
    )
    parser.add_argument("--all", action="store_true", help="Ingest ALL sources")
    parser.add_argument("--scam-sources", action="store_true", help="Ingest all scam/intelligence sources")
    parser.add_argument("--source", type=str, default="", help="Specific source name")
    parser.add_argument("--chain", type=str, default="all", help="Chain filter (ethereum, solana, bsc, etc.)")
    parser.add_argument("--report", action="store_true", help="Show label inventory report")
    parser.add_argument("--quiet", action="store_true", help="Reduce logging output")
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    # Connect to ClickHouse and Redis
    ch = _get_ch_client()
    redis = _get_redis_client()

    if not ch:
        logger.error("ClickHouse connection required. Set CLICKHOUSE_HOST/CLICKHOUSE_PORT env vars.")
        sys.exit(1)

    if not redis:
        logger.warning("Redis unavailable - labels will be stored in ClickHouse only")

    # Show report and exit
    if args.report:
        await show_report(ch)
        return

    # Determine which sources to run
    sources_to_run: list[tuple[str, Any]] = []

    if args.all:
        sources_to_run = list(SOURCE_REGISTRY.items())
    elif args.scam_sources:
        # Scam-specific sources
        scam_sources = [
            ("builtin_contracts", ingest_builtin_contracts),
            ("etherscan_labels", ingest_etherscan_labels),
            ("cryptoscamdb", ingest_cryptoscamdb),
            ("chainabuse", ingest_chainabuse),
            ("metamask_phishing", ingest_metamask_phishing),
            ("scanner_findings", ingest_scanner_findings),
            ("github_community", ingest_github_community),
        ]
        sources_to_run = scam_sources
    elif args.source:
        source_name = args.source.lower()
        if source_name in SOURCE_REGISTRY:
            sources_to_run = [(source_name, SOURCE_REGISTRY[source_name])]
        else:
            logger.error(f"Unknown source: {source_name}")
            logger.error(f"Available sources: {', '.join(sorted(SOURCE_REGISTRY.keys()))}")
            sys.exit(1)
    else:
        parser.print_help()
        return

    # Run each source
    grand_totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    start = time.time()

    for source_name, source_fn in sources_to_run:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"RUNNING: {source_name}")
        logger.info(f"{'=' * 60}")

        try:
            if source_name in ("etherscan_labels",):
                result = await source_fn(ch, redis, chain=args.chain)
            elif source_name in ("walletlabels_xyz", "bluepages"):
                result = await source_fn(ch, redis, chain=args.chain if args.chain != "all" else "ethereum")
            else:
                result = await source_fn(ch, redis)

            for key in grand_totals:
                grand_totals[key] += result.get(key, 0)

            elapsed = time.time() - start
            logger.info(
                f"  [{source_name}] {result.get('wallet_labels', 0):,} labels, "
                f"{result.get('scam_addresses', 0):,} scam addresses "
                f"(total elapsed: {elapsed:.1f}s)"
            )

        except Exception as e:
            logger.error(f"Source {source_name} failed: {e}", exc_info=True)

    elapsed = time.time() - start
    logger.info(f"\n{'=' * 60}")
    logger.info("INGESTION COMPLETE")
    logger.info(f"{'=' * 60}")
    logger.info(f"Total wallet_labels added: {grand_totals['wallet_labels']:,}")
    logger.info(f"Total scam_addresses added: {grand_totals['scam_addresses']:,}")
    logger.info(f"Total elapsed: {elapsed:.1f}s")

    # Show report after ingestion
    await show_report(ch)


if __name__ == "__main__":
    asyncio.run(main())
