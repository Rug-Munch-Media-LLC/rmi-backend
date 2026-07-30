#!/usr/bin/env python3
"""
Etherscan Label Ingestion Script
=================================
Reads consolidated Etherscan label CSVs and ingests each labeled address
into the RAG vector store using the same approach as the rekt/curated scripts.

- etherscan_phish_hack.csv  → "known_scams" collection
- etherscan_combined_labels.csv → "known_scams" (scam-adjacent labels) or
                                   "wallet_profiles" (all other labels)

Usage:
    python scripts/ingest_etherscan_labels.py [--phish-only] [--combined-only] [--limit N]

Options:
    --phish-only      Only ingest the phish-hack CSV (known_scams)
    --combined-only   Only ingest the combined labels CSV
    --limit N         Max rows to ingest per CSV (for testing)
"""

import argparse
import asyncio
import csv
import hashlib
import logging
import os
import sys

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ingest_etherscan_labels")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Scam-adjacent label keywords - these go to known_scams instead of wallet_profiles
SCAM_LABEL_KEYWORDS = [
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
    "whale-alert",
]


def is_scam_label(label: str) -> bool:
    """Check if a label category is scam-adjacent."""
    label_lower = label.lower()
    return any(kw in label_lower for kw in SCAM_LABEL_KEYWORDS)


def make_doc_id(collection: str, address: str, label: str = "", chain: str = "") -> str:
    """Stable doc ID based on collection + address + label (dedup safe)."""
    raw = f"{collection}:{address.lower()}:{label}:{chain}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def ingest_via_api(collection: str, content: str, metadata: dict, doc_id: str) -> dict:
    """
    Ingest a document via the backend HTTP API (same as rekt script pattern).
    Falls back to direct import if the API is unavailable.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "http://localhost:8000/api/v1/rag/ingest",
                json={
                    "collection": collection,
                    "content": content,
                    "metadata": metadata,
                },
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(f"API ingest failed ({resp.status_code}), falling back to direct")
    except Exception as e:
        logger.debug(f"API unavailable ({e}), falling back to direct import")

    # Direct import fallback
    from app.rag_service import ingest_document

    return await ingest_document(collection, content, metadata, doc_id)


async def ingest_phish_hack_csv(limit: int = 0) -> dict[str, int]:
    """Read etherscan_phish_hack.csv and ingest into known_scams collection."""
    csv_path = os.path.join(DATA_DIR, "etherscan_phish_hack.csv")
    if not os.path.exists(csv_path):
        logger.error(f"Phish-hack CSV not found: {csv_path}")
        return {"ingested": 0, "errors": 0}

    stats = {"ingested": 0, "errors": 0}
    rows = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if limit > 0:
        rows = rows[:limit]

    logger.info(f"Ingesting {len(rows)} phish-hack rows into known_scams...")

    for i, row in enumerate(rows):
        address = row["address"].strip()
        name_tag = row.get("name_tag", "").strip()
        label_type = row.get("label_type", "phish-hack").strip()
        chain = row.get("chain", "Ethereum").strip()
        balance = row.get("balance", "").strip()
        txn_count = row.get("txn_count", "").strip()

        if not address:
            continue

        content = (
            f"Etherscan {label_type} label: {address} on {chain}. "
            f"Tag: {name_tag}. Balance: {balance}. Txns: {txn_count}."
        )

        metadata = {
            "address": address.lower(),
            "label_type": label_type,
            "name_tag": name_tag,
            "chain": chain,
            "balance": balance,
            "txn_count": txn_count,
            "source": "etherscan",
            "severity": "high" if "phish" in label_type.lower() else "critical",
        }

        doc_id = make_doc_id("known_scams", address, label_type, chain)

        try:
            await ingest_via_api("known_scams", content, metadata, doc_id)
            stats["ingested"] += 1
            if (i + 1) % 20 == 0 or (i + 1) == len(rows):
                logger.info(f"  Progress: {i + 1}/{len(rows)} phish-hack rows ingested")
        except Exception as e:
            logger.error(f"Failed to ingest {address}: {e}")
            stats["errors"] += 1

    return stats


async def ingest_combined_labels_csv(limit: int = 0) -> dict[str, int]:
    """Read etherscan_combined_labels.csv and route to known_scams or wallet_profiles."""
    csv_path = os.path.join(DATA_DIR, "etherscan_combined_labels.csv")
    if not os.path.exists(csv_path):
        logger.error(f"Combined labels CSV not found: {csv_path}")
        return {"known_scams": 0, "wallet_profiles": 0, "errors": 0}

    stats = {"known_scams": 0, "wallet_profiles": 0, "errors": 0}
    rows = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if limit > 0:
        rows = rows[:limit]

    # Count what goes where
    scam_rows = [r for r in rows if is_scam_label(r.get("label", ""))]
    wallet_rows = [r for r in rows if not is_scam_label(r.get("label", ""))]

    logger.info(
        f"Ingesting {len(rows)} combined label rows: "
        f"{len(scam_rows)} → known_scams, {len(wallet_rows)} → wallet_profiles"
    )

    for i, row in enumerate(rows):
        address = row["address"].strip()
        name = row.get("name", "").strip()
        label = row.get("label", "").strip()
        chain = row.get("chain", "Ethereum").strip()

        if not address or not label:
            continue

        # Route to collection
        if is_scam_label(label):
            collection = "known_scams"
            content = (
                f"Etherscan scam label: {address} ({name}) on {chain}. "
                f"Category: {label}. Flagged as scam/phishing/exploit address."
            )
            metadata = {
                "address": address.lower(),
                "label": label,
                "name": name,
                "chain": chain,
                "source": "etherscan",
                "severity": "high",
                "label_type": "scam",
            }
        else:
            collection = "wallet_profiles"
            content = f"Etherscan labeled address: {address} ({name}) on {chain}. Category: {label}."
            metadata = {
                "address": address.lower(),
                "label": label,
                "name": name,
                "chain": chain,
                "source": "etherscan",
                "label_type": "profile",
            }

        doc_id = make_doc_id(collection, address, label, chain)

        try:
            await ingest_via_api(collection, content, metadata, doc_id)
            stats[collection] += 1
        except Exception as e:
            logger.error(f"Failed to ingest {address} ({label}): {e}")
            stats["errors"] += 1

        # Progress reporting
        if (i + 1) % 1000 == 0 or (i + 1) == len(rows):
            logger.info(
                f"  Progress: {i + 1}/{len(rows)} | "
                f"known_scams: {stats['known_scams']}, "
                f"wallet_profiles: {stats['wallet_profiles']}, "
                f"errors: {stats['errors']}"
            )

        # Yield to event loop periodically to avoid blocking
        if (i + 1) % 500 == 0:
            await asyncio.sleep(0)

    return stats


async def main():
    parser = argparse.ArgumentParser(description="Ingest Etherscan labels into RAG")
    parser.add_argument("--phish-only", action="store_true", help="Only ingest phish-hack CSV")
    parser.add_argument("--combined-only", action="store_true", help="Only ingest combined labels CSV")
    parser.add_argument("--limit", type=int, default=0, help="Max rows per CSV (0=all)")
    args = parser.parse_args()

    results = {}

    if not args.combined_only:
        logger.info("=" * 60)
        logger.info("INGESTING PHISH-HACK LABELS → known_scams")
        logger.info("=" * 60)
        phish_stats = await ingest_phish_hack_csv(limit=args.limit)
        results["phish_hack"] = phish_stats
        logger.info(f"Phish-hack complete: {phish_stats}")

    if not args.phish_only:
        logger.info("=" * 60)
        logger.info("INGESTING COMBINED LABELS → known_scams + wallet_profiles")
        logger.info("=" * 60)
        combined_stats = await ingest_combined_labels_csv(limit=args.limit)
        results["combined_labels"] = combined_stats
        logger.info(f"Combined labels complete: {combined_stats}")

    logger.info("=" * 60)
    logger.info(f"ALL INGESTION COMPLETE: {results}")
    logger.info("=" * 60)
    return results


if __name__ == "__main__":
    asyncio.run(main())
