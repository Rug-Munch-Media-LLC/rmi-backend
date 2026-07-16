"""
Wallet Label Loader - Loads curated wallet address labels from open-source datasets.
Sources:
  - crypto-wallet-address-labels (105K+ Solana, 16K BSC/ETH, 15K phishing)
  - OFAC crypto-sanctions-list
"""

import csv
import json
import os

from app.core.logging import get_logger

logger = get_logger(__name__)

LABELS_BASE = "/app/data/wallet-labels"


def load_all_labels(redis_client) -> dict:
    """Load all wallet label datasets into Redis. Returns counts."""
    counts = {}

    # 1. Solana CEX labels
    path = f"{LABELS_BASE}/solana_cex_labels.csv"
    if os.path.exists(path):
        count = _load_csv_labels(
            redis_client,
            path,
            "solana",
            "address",
            {
                "label_type": "label_type",
                "label_subtype": "label_subtype",
                "name": "address_name",
                "project": "project_name",
            },
        )
        counts["solana_cex"] = count

    # 2. Solana DApp labels
    path = f"{LABELS_BASE}/solana_dapp_labels.csv"
    if os.path.exists(path):
        count = _load_csv_labels(redis_client, path, "solana", "address", {"name": "dapp_name"})
        counts["solana_dapp"] = count

    # 3. Solana DeFi labels
    path = f"{LABELS_BASE}/solana_defi_labels.csv"
    if os.path.exists(path):
        count = _load_csv_labels(redis_client, path, "solana", "address", {"name": "protocol_name"})
        counts["solana_defi"] = count

    # 4. Ethereum phishing and scam
    path = f"{LABELS_BASE}/etherscan_malicious_labels.csv"
    if os.path.exists(path):
        count = _load_csv_labels(
            redis_client,
            path,
            "ethereum",
            "banned_address",
            {"tag": "wallet_tag", "source": "data_source"},
        )
        counts["eth_phishing"] = count

    # 5. Malicious smart contracts
    path = f"{LABELS_BASE}/malicious_smart_contracts.csv"
    if os.path.exists(path):
        count = _load_csv_labels(
            redis_client,
            path,
            "ethereum",
            "contract_address",
            {"tag": "contract_tag", "notes": "notes", "source": "source"},
        )
        counts["eth_malicious_contracts"] = count

    # 6. OFAC sanctions
    path = f"{LABELS_BASE}/ofac_sanctions.json"
    if os.path.exists(path):
        try:
            with open(path) as f:
                sanctions = json.load(f)
            for entry in sanctions:
                addr = entry.get("address", "")
                if addr:
                    redis_client.set(f"rmi:label:ofac:{addr.lower()}", json.dumps(entry))
                    redis_client.sadd("rmi:labels:ofac", addr.lower())
            redis_client.set("rmi:labels:ofac:count", len(sanctions))
            counts["ofac"] = len(sanctions)
        except Exception as e:
            logger.warning(f"OFAC load failed: {e}")

    # Store total counts
    redis_client.set("rmi:labels:counts", json.dumps(counts))
    logger.info(f"Loaded wallet labels: {counts}")
    return counts


def _load_csv_labels(
    redis_client, path: str, chain: str, addr_col: str, label_cols: dict[str, str]
) -> int:
    """Load a CSV of labeled addresses into Redis."""
    count = 0
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row.get(addr_col, "").strip().lower()
                if not addr or len(addr) < 10:
                    continue

                # Build label entry
                label = {"chain": chain, "address": addr}
                for key, col in label_cols.items():
                    val = row.get(col, "").strip()
                    if val:
                        label[key] = val

                redis_client.set(f"rmi:label:{chain}:{addr}", json.dumps(label))
                redis_client.sadd(f"rmi:labels:{chain}", addr)
                count += 1
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
    return count


def lookup_wallet_label(address: str, chain: str | None = None, redis_client=None) -> dict | None:
    """Look up a wallet address in the label database."""
    if not redis_client:
        return None

    addr = address.lower().strip()

    # Check specific chain
    if chain:
        label = redis_client.get(f"rmi:label:{chain}:{addr}")
        if label:
            return json.loads(label)

    # Check all chains
    for ch in ["solana", "ethereum", "bsc"]:
        label = redis_client.get(f"rmi:label:{ch}:{addr}")
        if label:
            return json.loads(label)

    # Check OFAC
    ofac = redis_client.get(f"rmi:label:ofac:{addr}")
    if ofac:
        result = json.loads(ofac)
        result["is_sanctioned"] = True
        return result

    return None
