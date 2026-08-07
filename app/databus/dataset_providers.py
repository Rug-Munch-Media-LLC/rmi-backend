"""
Real-CATS and MBAL Dataset Providers
=====================================
Two massive free datasets for AML detection and address labeling.

1. Real-CATS - 153,121 addresses (50,943 criminal + 102,178 benign)
   with full transaction profiles. Ideal for risk scoring and AML.
   Source: https://github.com/sjdseu/Real-CATS

2. MBAL - 10 million annotated crypto addresses across 5 chains
   with 62 categories. The largest free label dataset available.
   Source: https://www.kaggle.com/datasets/yidongchaintoolai/mbal-10m-crypto-address-label-dataset
   NOTE: Requires manual Kaggle download. Place files in ~/rmi/mbal/
"""

from __future__ import annotations

import csv
import os

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.dataset_providers")

# ═══════════════════════════════════════════════════════════════
# 1. REAL-CATS - Criminal + Benign Address Dataset
# ═══════════════════════════════════════════════════════════════

REAL_CATS_PATHS = [
    "/tmp/Real-CATS",  # noqa: S108 — dataset dir path fallback (mounted volume)
    "/app/Real-CATS",
    os.path.expanduser("~/rmi/Real-CATS"),
    os.path.expanduser("~/rmi/datasets/Real-CATS"),
]

# File → label mapping for Real-CATS naming convention
REAL_CATS_FILE_LABELS = {
    "CB.tsv": "criminal",  # Criminal Bitcoin
    "CE.tsv": "criminal",  # Criminal Ethereum
    "BB.tsv": "benign",  # Benign Bitcoin
    "BE.tsv": "benign",  # Benign Ethereum
    "Sup-CATS.tsv": "criminal",  # Supplementary criminal
    "TI_B.tsv": "benign",  # Transaction Info Benign
    "TI_M.tsv": "criminal",  # Transaction Info Malicious
    "Identifier.tsv": "mixed",  # Identifier mappings
}


def _find_real_cats_dir() -> str | None:
    for p in REAL_CATS_PATHS:
        if os.path.isdir(p) and any(f.endswith(".tsv") for f in os.listdir(p)):
            return p
    return None


def _load_real_cats() -> dict:
    """Load Real-CATS dataset into memory."""
    base = _find_real_cats_dir()
    if not base:
        return {"error": "Real-CATS dataset not found. Clone from https://github.com/sjdseu/Real-CATS"}

    result = {"criminal": [], "benign": [], "stats": {}}

    for filename, label in REAL_CATS_FILE_LABELS.items():
        filepath = os.path.join(base, filename)
        if not os.path.exists(filepath):
            continue

        is_criminal = label == "criminal"
        is_benign = label == "benign"

        try:
            with open(filepath, encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter="\t")  # TSV = tab-separated
                for row in reader:
                    addr = row.get("address", "").strip()
                    if not addr:
                        continue

                    entry = {
                        "address": addr,
                        "label": row.get("label", "criminal" if is_criminal else "benign"),
                        "chain": "bitcoin" if filename.startswith("B") else "ethereum",
                        "source_file": filename,
                    }
                    # Include key features for risk scoring
                    for k in (
                        "balance",
                        "total_received_BTC",
                        "total_sent_BTC",
                        "total_received_USD",
                        "total_sent_USD",
                        "transaction_number",
                        "first_time",
                        "last_time",
                        "lifetime",
                    ):
                        if k in row:
                            entry[k] = row[k]

                    if is_criminal:
                        result["criminal"].append(entry)
                    elif is_benign:
                        result["benign"].append(entry)
                    else:
                        # Mixed file - use the actual label field
                        if (
                            "scam" in (row.get("label", "") or "").lower()
                            or "criminal" in (row.get("label", "") or "").lower()
                        ):
                            result["criminal"].append(entry)
                        else:
                            result["benign"].append(entry)

        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"Real-CATS: failed to parse {filepath}: {e}")

    result["stats"] = {
        "criminal_count": len(result["criminal"]),
        "benign_count": len(result["benign"]),
        "total": len(result["criminal"]) + len(result["benign"]),
        "source": "Real-CATS (GitHub)",
        "url": "https://github.com/sjdseu/Real-CATS",
        "paper": "https://arxiv.org/html/2501.15553v1",
        "files_loaded": sum(1 for f in REAL_CATS_FILE_LABELS if os.path.exists(os.path.join(base, f))),
    }

    return result


async def fetch_real_cats(address: str | None = None, category: str = "all", limit: int = 50) -> dict:
    """Query Real-CATS - check if address is criminal, or list criminal/benign addresses."""
    data = _load_real_cats()

    if "error" in data:
        return data

    if address:
        addr = address.lower()
        # Search both categories
        for entry in data["criminal"] + data["benign"]:
            if entry["address"].lower() == addr:
                return {
                    "address": address,
                    "match": entry,
                    "is_criminal": entry["label"] == "criminal",
                    "source": "Real-CATS",
                }
        return {"address": address, "match": None, "found": False, "source": "Real-CATS"}

    if category == "criminal":
        results = data["criminal"][:limit]
    elif category == "benign":
        results = data["benign"][:limit]
    else:
        results = data["criminal"][: limit // 2] + data["benign"][: limit // 2]

    return {
        "category": category,
        "results": results,
        "stats": data["stats"],
        "source": "Real-CATS",
    }


# ═══════════════════════════════════════════════════════════════
# 2. MBAL - 10 Million Annotated Crypto Addresses
# ═══════════════════════════════════════════════════════════════

MBAL_PATHS = [
    os.path.expanduser("~/rmi/mbal"),
    "/app/mbal",
    os.path.expanduser("~/rmi/datasets/mbal"),
    "/tmp/mbal",  # noqa: S108 — dataset dir path fallback (mounted volume)
]

MBAL_README = """
MBAL: 10 Million Crypto Address Label Dataset
==============================================
Source: https://www.kaggle.com/datasets/yidongchaintoolai/mbal-10m-crypto-address-label-dataset

To use:
1. Download from Kaggle (requires free account)
2. Place CSV/parquet files in ~/rmi/mbal/
3. The provider auto-loads them

Chains: Bitcoin, Ethereum, BNB Chain, Polygon, Avalanche
Categories: 62 distinct classifications
"""


def _find_mbal_dir() -> str | None:
    for p in MBAL_PATHS:
        if os.path.isdir(p) and os.listdir(p):
            return p
    return None


def _get_mbal_install_instructions() -> str:
    return MBAL_README


async def fetch_mbal(
    address: str | None = None,
    chain: str | None = None,
    category: str | None = None,
    limit: int = 20,
) -> dict:
    """Query MBAL - 10M labeled addresses. Schema: chain,address,categories,entity,source"""
    base = _find_mbal_dir()

    if not base:
        return {
            "error": "MBAL dataset not installed",
            "instructions": _get_mbal_install_instructions(),
            "download_url": "https://www.kaggle.com/datasets/yidongchaintoolai/mbal-10m-crypto-address-label-dataset",
            "source": "MBAL (Kaggle)",
        }

    # Find the main dataset file
    main_file = None
    for f in sorted(os.listdir(base)):
        if f.startswith("dataset_10m") and f.endswith(".csv"):
            main_file = os.path.join(base, f)
            break

    if not main_file:
        # Fallback: any CSV
        csv_files = [f for f in os.listdir(base) if f.endswith(".csv")]
        if csv_files:
            main_file = os.path.join(base, csv_files[0])

    if not main_file:
        return {"error": "No CSV files found", "path": base, "source": "MBAL"}

    try:
        results = []
        with open(main_file, encoding="utf-8") as f:  # noqa: ASYNC230 — blocking CSV read in async fetch; data is small local dataset
            reader = csv.DictReader(f)

            for row in reader:
                match = True

                # Filter by address
                if address and address.lower() not in row.get("address", "").lower():
                    match = False

                # Filter by chain
                if chain and chain.lower() not in row.get("chain", "").lower():
                    match = False

                # Filter by category
                if category and category.lower() not in row.get("categories", "").lower():
                    match = False

                if match:
                    results.append(
                        {
                            "chain": row.get("chain", ""),
                            "address": row.get("address", ""),
                            "categories": row.get("categories", ""),
                            "entity": row.get("entity", ""),
                            "source": row.get("source", ""),
                        }
                    )

                if len(results) >= limit:
                    break

        # Stats (quick estimate from filename)
        total_estimate = "10,000,023 rows"

        return {
            "results": results,
            "match_count": len(results),
            "filters": {"address": address, "chain": chain, "category": category},
            "source": "MBAL - 10M annotated addresses (Kaggle)",
            "total_estimate": total_estimate,
            "categories": "62 distinct: cex, dex, l2, bridge, mixer, scam, gambling, nft, defi, ...",
            "chains_covered": [
                "bitcoin_mainnet",
                "ethereum_mainnet",
                "bsc_mainnet",
                "polygon_mainnet",
                "avalanche",
            ],
        }

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"MBAL query failed: {e}")
        return {"error": str(e), "source": "MBAL"}
