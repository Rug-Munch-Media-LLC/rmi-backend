#!/usr/bin/env python3
"""
Wallet Label Data Quality Fixer
================================
Fixes:
1. Column name mismatches (ADDRESS vs address, banned_address vs address)
2. Files without headers (exchange.csv, dex.csv, mining*.csv, etc.)
3. Deduplicates within each file
4. Deduplicates across files (keeps best label)
5. Validates addresses (length, format, chain detection)
6. Adds entity type labels where missing
7. Produces clean consolidated output

Chain detection:
- 32-44 chars base58 starting with 1-9 → Solana
- 42 chars starting with 0x → EVM
- 42 chars starting with T → TRON
"""

import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime

DATA_DIR = "/root/backend/data"
LABEL_DIR = os.path.join(DATA_DIR, "wallet-labels")
CLEAN_DIR = os.path.join(DATA_DIR, "wallet-labels-clean")

# Entity type mapping
ENTITY_TYPES = {
    "cex": "exchange",
    "deposit_wallet": "exchange_deposit",
    "dex": "dex",
    "defi": "defi",
    "dapp": "dapp",
    "phishing": "phishing_scam",
    "malicious": "malicious",
    "scam": "scam",
    "hack": "exploiter",
    "exploit": "exploiter",
    "mining": "mining_pool",
    "miner": "mining_pool",
    "ico": "ico_wallet",
    "exchange": "exchange",
    "walletapp": "wallet_app",
    "bridge": "bridge",
    "lending": "lending",
    "staking": "staking",
    "governance": "governance",
    "router": "router",
    "pool": "liquidity_pool",
    "farm": "yield_farm",
    "nft": "nft",
    "gaming": "gaming",
    "oracle": "oracle",
    "stablecoin": "stablecoin",
    "wrapped": "wrapped_token",
}

# Source quality ranking (higher = more trusted)
SOURCE_RANK = {
    "ofac": 100,
    "etherscan_phish_hack": 90,
    "etherscan_malicious": 85,
    "phishing_scams": 80,
    "malicious_smart_contracts": 75,
    "etherscan_combined": 70,
    "solana_cex": 65,
    "solana_defi": 60,
    "solana_dapp": 55,
    "exchanges_cluster": 50,
    "exchange": 45,
    "dex": 40,
    "icowallets": 35,
    "mining": 30,
    "walletapp": 25,
}


def detect_chain(address: str) -> str:
    """Detect blockchain from address format."""
    addr = address.strip()
    if addr.startswith("0x") and len(addr) == 42:
        return "ethereum"
    if addr.startswith("T") and len(addr) == 34:
        return "tron"
    if 32 <= len(addr) <= 44 and not addr.startswith("0x"):
        return "solana"
    return "unknown"


def is_valid_address(address: str) -> bool:
    """Basic address validation."""
    addr = address.strip()
    if not addr:
        return False
    # EVM: 0x + 40 hex chars
    if addr.startswith("0x"):
        if len(addr) != 42:
            return False
        return all(c in "0123456789abcdefABCDEF" for c in addr[2:])
    # Solana: 32-44 base58 chars
    return 32 <= len(addr) <= 44


def infer_entity_type(row: dict, source: str) -> str:
    """Infer entity type from label data."""
    # Check explicit type fields
    for field in ["label_type", "label_subtype", "type", "category", "tag"]:
        val = (row.get(field, "") or "").lower()
        for key, entity in ENTITY_TYPES.items():
            if key in val:
                return entity

    # Check name fields
    for field in [
        "name",
        "address_name",
        "project",
        "label",
        "protocol_name",
        "dapp_name",
        "exchange_name",
    ]:
        val = (row.get(field, "") or "").lower()
        for key, entity in ENTITY_TYPES.items():
            if key in val:
                return entity

    # Source-based fallback
    source_map = {
        "solana_cex": "exchange",
        "solana_defi": "defi",
        "solana_dapp": "dapp",
        "phishing_scams": "phishing_scam",
        "etherscan_malicious": "malicious",
        "malicious_smart_contracts": "malicious",
        "exchanges_cluster": "exchange",
        "exchange": "exchange",
        "dex": "dex",
        "mining": "mining_pool",
        "icowallets": "ico_wallet",
        "walletapp": "wallet_app",
    }
    return source_map.get(source, "unknown")


def load_solana_csvs():
    """Load Solana label CSVs with correct column names."""
    labels = []

    solana_specs = [
        ("solana_cex_labels.csv", "solana_cex"),
        ("solana_dapp_labels.csv", "solana_dapp"),
        ("solana_defi_labels.csv", "solana_defi"),
    ]

    for filename, source in solana_specs:
        path = os.path.join(LABEL_DIR, filename)
        if not os.path.exists(path):
            print(f"  MISSING: {filename}")
            continue

        seen = set()
        dupe_count = 0
        invalid_count = 0

        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Solana files use uppercase ADDRESS
                addr = (row.get("ADDRESS") or row.get("address") or "").strip()

                if not is_valid_address(addr):
                    invalid_count += 1
                    continue

                addr_lower = addr.lower()
                if addr_lower in seen:
                    dupe_count += 1
                    continue
                seen.add(addr_lower)

                labels.append(
                    {
                        "address": addr,
                        "chain": "solana",
                        "source": source,
                        "name": row.get("ADDRESS_NAME") or row.get("address_name") or "",
                        "label_type": row.get("LABEL_TYPE") or row.get("label_type") or "",
                        "label_subtype": row.get("LABEL_SUBTYPE") or row.get("label_subtype") or "",
                        "project": row.get("PROJECT_NAME") or row.get("project_name") or "",
                        "entity_type": infer_entity_type(row, source),
                    }
                )

        print(f"  {filename}: {len(seen):,} unique addresses ({dupe_count} dupes removed, {invalid_count} invalid)")

    return labels


def load_etherscan_data():
    """Load Etherscan label data."""
    labels = []

    # Combined labels
    path = os.path.join(DATA_DIR, "etherscan_combined_labels.csv")
    if os.path.exists(path):
        seen = set()
        dupe_count = 0
        invalid_count = 0

        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        lines = content.split("\n")

        # Detect header
        if lines and lines[0]:
            first = lines[0]
            if any(keyword in first.lower() for keyword in ["address", "label", "name", "tag"]):
                first.split(",")
                lines = lines[1:]

        for line in lines:
            parts = line.split(",")
            if len(parts) < 2:
                invalid_count += 1
                continue

            # Try multiple column positions
            addr = parts[0].strip().strip('"')
            if not addr.startswith("0x"):
                # Try to find address in this row
                for p in parts:
                    if p.strip().startswith("0x") and len(p.strip()) == 42:
                        addr = p.strip()
                        break

            if not is_valid_address(addr):
                invalid_count += 1
                continue

            addr_lower = addr.lower()
            if addr_lower in seen:
                dupe_count += 1
                continue
            seen.add(addr_lower)

            # Get label from column 1 or label column
            label = parts[1].strip().strip('"') if len(parts) > 1 else ""

            labels.append(
                {
                    "address": addr,
                    "chain": "ethereum",
                    "source": "etherscan_combined",
                    "name": label,
                    "label": label,
                    "entity_type": infer_entity_type({"label": label}, "etherscan_combined"),
                }
            )

        print(
            f"  etherscan_combined_labels.csv: {len(seen):,} unique addresses ({dupe_count} dupes, {invalid_count} invalid)"
        )

    # Malicious labels
    mal_path = os.path.join(LABEL_DIR, "etherscan_malicious_labels.csv")
    if os.path.exists(mal_path):
        seen = set()
        dupe_count = 0

        with open(mal_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = (row.get("banned_address") or row.get("address") or "").strip()

                if not is_valid_address(addr):
                    continue

                addr_lower = addr.lower()
                if addr_lower in seen:
                    dupe_count += 1
                    continue
                seen.add(addr_lower)

                labels.append(
                    {
                        "address": addr,
                        "chain": "ethereum",
                        "source": "etherscan_malicious",
                        "name": row.get("wallet_tag", ""),
                        "entity_type": "malicious",
                    }
                )

        print(f"  etherscan_malicious_labels.csv: {len(seen):,} unique addresses ({dupe_count} dupes)")

    return labels


def load_no_header_csvs():
    """Load CSVs without headers (exchange, dex, mining, etc.)."""
    labels = []

    specs = [
        ("exchange.csv", "exchange", 0, 1),
        ("dex.csv", "dex", 0, 1),
        ("icowallets.csv", "icowallets", 0, 1),
        ("mining1.csv", "mining", 0, 1),
        ("mining2.csv", "mining", 0, 1),
        ("walletapp.csv", "walletapp", 0, 1),
        ("exchanges_cluster.csv", "exchanges_cluster", 0, 1),
    ]

    for filename, source, addr_col, label_col in specs:
        path = os.path.join(LABEL_DIR, filename)
        if not os.path.exists(path):
            print(f"  MISSING: {filename}")
            continue

        seen = set()
        invalid = 0

        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue

            parts = line.split(",")
            if len(parts) < 2:
                continue

            addr = parts[addr_col].strip().strip('"')
            if not is_valid_address(addr):
                invalid += 1
                continue

            addr_lower = addr.lower()
            if addr_lower in seen:
                continue
            seen.add(addr_lower)

            label = parts[label_col].strip().strip('"') if len(parts) > label_col else ""

            labels.append(
                {
                    "address": addr,
                    "chain": detect_chain(addr),
                    "source": source,
                    "name": label,
                    "entity_type": infer_entity_type({"name": label}, source),
                }
            )

        print(f"  {filename}: {len(seen):,} unique ({invalid} invalid)")

    return labels


def load_phishing_scams():
    """Load phishing scam labels."""
    labels = []
    path = os.path.join(LABEL_DIR, "phishing_scams.csv")
    if not os.path.exists(path):
        return labels

    seen = set()
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            addr = (row.get("address") or row.get("Address") or "").strip()
            if not is_valid_address(addr):
                continue

            addr_lower = addr.lower()
            if addr_lower in seen:
                continue
            seen.add(addr_lower)

            labels.append(
                {
                    "address": addr,
                    "chain": detect_chain(addr),
                    "source": "phishing_scams",
                    "name": row.get("name", row.get("label", "")),
                    "entity_type": "phishing_scam",
                }
            )

    print(f"  phishing_scams.csv: {len(seen):,} unique")
    return labels


def load_malicious_contracts():
    """Load malicious smart contract labels."""
    labels = []
    path = os.path.join(LABEL_DIR, "malicious_smart_contracts.csv")
    if not os.path.exists(path):
        return labels

    seen = set()
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            addr = (row.get("contract_address") or row.get("address") or "").strip()
            if not is_valid_address(addr):
                continue

            addr_lower = addr.lower()
            if addr_lower in seen:
                continue
            seen.add(addr_lower)

            labels.append(
                {
                    "address": addr,
                    "chain": detect_chain(addr),
                    "source": "malicious_smart_contracts",
                    "name": row.get("name", row.get("label", "")),
                    "entity_type": "malicious",
                }
            )

    print(f"  malicious_smart_contracts.csv: {len(seen):,} unique")
    return labels


def merge_and_deduplicate(all_labels):
    """Merge all labels, deduplicate across sources, keep highest quality source."""

    # Group by address
    by_address = defaultdict(list)
    for label in all_labels:
        addr = label["address"].lower()
        by_address[addr].append(label)

    merged = []
    dupes_resolved = 0

    for addr, entries in by_address.items():
        if len(entries) == 1:
            merged.append(entries[0])
        else:
            dupes_resolved += len(entries) - 1
            # Keep highest ranked source
            entries.sort(key=lambda e: SOURCE_RANK.get(e["source"], 0), reverse=True)
            best = entries[0]
            # Merge entity types from other sources
            all_entities = set()
            for e in entries:
                et = e.get("entity_type", "")
                if et and et != "unknown":
                    all_entities.add(et)
            if all_entities:
                best["entity_types"] = list(all_entities)
            merged.append(best)

    return merged, dupes_resolved


def main():
    print("=== Wallet Label Data Quality Fixer ===\n")

    os.makedirs(CLEAN_DIR, exist_ok=True)

    # Load all sources
    print("Loading data sources...")
    all_labels = []

    all_labels.extend(load_solana_csvs())
    all_labels.extend(load_etherscan_data())
    all_labels.extend(load_no_header_csvs())
    all_labels.extend(load_phishing_scams())
    all_labels.extend(load_malicious_contracts())

    print(f"\nTotal raw labels: {len(all_labels):,}")

    # Chain distribution
    chains = Counter(l["chain"] for l in all_labels)
    print(f"Chain distribution: {dict(chains)}")

    # Source distribution
    sources = Counter(l["source"] for l in all_labels)
    print("Source distribution:")
    for src, cnt in sources.most_common():
        print(f"  {src}: {cnt:,}")

    # Entity type distribution
    entities = Counter(l.get("entity_type", "unknown") for l in all_labels)
    print("\nEntity type distribution:")
    for ent, cnt in entities.most_common():
        print(f"  {ent}: {cnt:,}")

    # Merge and deduplicate
    print("\nDeduplicating across sources...")
    merged, dupes_resolved = merge_and_deduplicate(all_labels)
    print(f"Resolved {dupes_resolved:,} cross-source duplicates")
    print(f"Final unique addresses: {len(merged):,}")

    # Save clean CSVs by chain
    for chain in ["ethereum", "solana", "tron", "unknown"]:
        chain_labels = [l for l in merged if l["chain"] == chain]
        if not chain_labels:
            continue

        outpath = os.path.join(CLEAN_DIR, f"wallet_labels_{chain}.csv")
        with open(outpath, "w", newline="") as f:
            fields = [
                "address",
                "chain",
                "source",
                "name",
                "label_type",
                "label_subtype",
                "project",
                "entity_type",
                "entity_types",
            ]
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for label in sorted(chain_labels, key=lambda l: l["address"]):
                writer.writerow(label)

        print(f"  Saved {outpath}: {len(chain_labels):,} labels")

    # Save master JSON
    master_path = os.path.join(CLEAN_DIR, "wallet_labels_master.json")
    with open(master_path, "w") as f:
        json.dump(merged, f, indent=2, default=str)
    print(f"  Saved {master_path}: {len(merged):,} labels")

    # Save manifest
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "total_labels": len(merged),
        "chains": dict(chains),
        "sources": dict(sources),
        "entity_types": dict(entities),
        "dupes_resolved": dupes_resolved,
        "files": os.listdir(CLEAN_DIR),
    }
    with open(os.path.join(CLEAN_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print("\n=== Done ===")
    print(f"Clean data saved to {CLEAN_DIR}/")
    print(f"Total: {len(merged):,} unique addresses across {len(chains)} chains")
    print(f"Dupes resolved: {dupes_resolved:,}")


if __name__ == "__main__":
    main()
