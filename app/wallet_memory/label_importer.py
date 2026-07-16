"""
Static Label Importer - Async boot-time loader for 105K+ wallet labels.
========================================================================
Replaces the sync Redis-only wallet_label_loader.py with an async module that:

1. Downloads/locates crypto-wallet-address-labels datasets (Solana CEX, DApp, DeFi)
2. Loads etherscan CSVs from /root/backend/data/
3. Fetches OFAC crypto-sanctions-list
4. Stores everything through WalletStorage (Redis cache + ClickHouse)

Sources:
  - crypto-wallet-address-labels GitHub (105K+ Solana)
  - etherscan_combined_labels.csv (50K rows)
  - etherscan_phish_hack.csv (111 rows)
  - OFAC crypto-sanctions-list
"""

import contextlib
import csv
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("wallet_memory.label_importer")

# ── Paths ──────────────────────────────────────────────────────────────

DATA_DIR = os.getenv("RMI_DATA_DIR", "/app/data")
LABELS_DIR = os.path.join(DATA_DIR, "wallet-labels")
ETHERSCAN_COMBINED = os.path.join(DATA_DIR, "etherscan_combined_labels.csv")
ETHERSCAN_PHISH_HACK = os.path.join(DATA_DIR, "etherscan_phish_hack.csv")

# GitHub base for Solana label CSVs
SOLANA_LABELS_REPO = (
    "https://raw.githubusercontent.com/ImMike/"
    "crypto-wallet-address-labels/main/datasets/solana-wallet-and-program-labels"
)

# Additional ImMike repo dataset paths
BSC_LABELS_REPO = "https://raw.githubusercontent.com/ImMike/crypto-wallet-address-labels/main/datasets/bsc-ethereum-address-tags"
ETH_EXCHANGE_LABELS_REPO = (
    "https://raw.githubusercontent.com/ImMike/"
    "crypto-wallet-address-labels/main/datasets/ethereum-exchange-and-dex-labels"
)
ETH_CLUSTER_LABELS_REPO = (
    "https://raw.githubusercontent.com/ImMike/"
    "crypto-wallet-address-labels/main/datasets/ethereum-cluster-exchange-labels"
)
ETH_CONTRACT_LABELS_REPO = "https://raw.githubusercontent.com/ImMike/crypto-wallet-address-labels/main/datasets/ethereum-contract-name-labels"

# OFAC sanctions URL
OFAC_URL = (
    "https://raw.githubusercontent.com/OFAC/crypto-sanctions-list/main/crypto-sanctions-list.json"
)

# Solana label file specs: (filename, chain, address_col, label_col_map)
SOLANA_CSV_SPECS = [
    (
        "solana_cex_labels.csv",
        "solana",
        "address",
        {
            "label_type": "label_type",
            "label_subtype": "label_subtype",
            "name": "address_name",
            "project": "project_name",
        },
    ),
    (
        "solana_dapp_labels.csv",
        "solana",
        "address",
        {
            "name": "dapp_name",
        },
    ),
    (
        "solana_defi_labels.csv",
        "solana",
        "address",
        {
            "name": "protocol_name",
        },
    ),
]

# Additional Ethereum phishing/scam CSV specs from the same repo
ETH_CSV_SPECS = [
    (
        "phishing_scams.csv",
        "ethereum",
        "address",
        {
            "name": "etherscan_tag",
            "label_type": "etherscan_labels",
        },
    ),
    (
        "etherscan_malicious_labels.csv",
        "ethereum",
        "banned_address",
        {
            "name": "wallet_tag",
            "label_type": "data_source",
        },
    ),
    (
        "malicious_smart_contracts.csv",
        "ethereum",
        "contract_address",
        {
            "name": "contract_tag",
            "label_type": "source",
        },
    ),
]

# ClickHouse DDL for wallet_labels table (added to schema on first run)
WALLET_LABELS_DDL = """
CREATE TABLE IF NOT EXISTS wallet_labels (
    address         String,
    chain_id        LowCardinality(String),
    label_name      String DEFAULT '',
    label_category  LowCardinality(String) DEFAULT '',
    label_subtype   String DEFAULT '',
    source          LowCardinality(String) DEFAULT '',
    is_sanctioned   UInt8 DEFAULT 0,
    loaded_at       DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY (address, chain_id, source);
"""

# ── Download helpers ────────────────────────────────────────────────────

_http_client: httpx.AsyncClient | None = None


async def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "RMI-LabelImporter/1.0"},
        )
    return _http_client


async def _download_file(url: str, dest: str) -> bool:
    """Download a URL to dest path. Returns True on success."""
    try:
        client = await _get_http()
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"Download failed {url}: HTTP {resp.status_code}")
            return False
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(resp.content)
        logger.info(f"Downloaded {url} -> {dest} ({len(resp.content)} bytes)")
        return True
    except Exception as e:
        logger.warning(f"Download error {url}: {e}")
        return False


async def _download_json(url: str) -> Any | None:
    """Download and parse JSON from URL. Returns None on failure."""
    try:
        client = await _get_http()
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"JSON download failed {url}: HTTP {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        logger.warning(f"JSON download error {url}: {e}")
        return None


# ── Solana labels ──────────────────────────────────────────────────────


async def _ensure_solana_labels() -> dict[str, int]:
    """Download Solana label CSVs if not present. Returns {filename: size}."""
    os.makedirs(LABELS_DIR, exist_ok=True)
    results = {}
    for filename, _, _, _ in SOLANA_CSV_SPECS:
        dest = os.path.join(LABELS_DIR, filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            size = os.path.getsize(dest)
            logger.info(f"Solana labels already present: {filename} ({size} bytes)")
            results[filename] = size
        else:
            url = f"{SOLANA_LABELS_REPO}/{filename}"
            ok = await _download_file(url, dest)
            results[filename] = os.path.getsize(dest) if ok else 0
            if not ok:
                logger.warning(f"Failed to download {filename} - skipping")
    return results


async def _ensure_eth_scam_labels() -> dict[str, int]:
    """Download ETH phishing/scam CSVs if not present."""
    eth_scam_repo = (
        "https://raw.githubusercontent.com/ImMike/"
        "crypto-wallet-address-labels/main/datasets/ethereum-phishing-and-scam-labels"
    )
    os.makedirs(LABELS_DIR, exist_ok=True)
    results = {}
    for filename, _, _, _ in ETH_CSV_SPECS:
        dest = os.path.join(LABELS_DIR, filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            size = os.path.getsize(dest)
            logger.info(f"ETH scam labels already present: {filename} ({size} bytes)")
            results[filename] = size
        else:
            url = f"{eth_scam_repo}/{filename}"
            ok = await _download_file(url, dest)
            results[filename] = os.path.getsize(dest) if ok else 0
            if not ok:
                logger.warning(f"Failed to download {filename} - skipping")
    return results


# ── OFAC sanctions ─────────────────────────────────────────────────────


async def _load_ofac_sanctions(storage) -> int:
    """
    Load OFAC crypto-sanctions-list into storage.
    Tries local file first, then downloads from GitHub.
    """
    # Try local cached copy first
    local_ofac = os.path.join(LABELS_DIR, "ofac_sanctions.json")
    sanctions = None

    if os.path.exists(local_ofac) and os.path.getsize(local_ofac) > 0:
        try:
            with open(local_ofac, encoding="utf-8") as f:
                sanctions = json.load(f)
            logger.info(f"Loaded OFAC from local cache ({len(sanctions)} entries)")
        except Exception as e:
            logger.warning(f"Local OFAC file corrupt: {e}")
            sanctions = None

    if sanctions is None:
        sanctions = await _download_json(OFAC_URL)
        if sanctions is None:
            logger.warning("OFAC sanctions download failed - trying fallback")
            # Fallback: try alternate URL formats
            alt_urls = [
                "https://raw.githubusercontent.com/OFAC/crypto-sanctions-list/master/crypto-sanctions-list.json",
                "https://raw.githubusercontent.com/OFAC/crypto-sanctions-list/main/crypto_sanctions_list.json",
            ]
            for alt in alt_urls:
                sanctions = await _download_json(alt)
                if sanctions is not None:
                    break

    if sanctions is None:
        logger.error("OFAC sanctions unavailable from all sources")
        return 0

    # Save local cache for next boot
    try:
        os.makedirs(LABELS_DIR, exist_ok=True)
        with open(local_ofac, "w", encoding="utf-8") as f:
            json.dump(sanctions, f)
    except Exception:
        pass

    # Store in Redis and ClickHouse
    count = 0
    ch_rows = []
    for entry in sanctions:
        addr = ""
        if isinstance(entry, dict):
            addr = entry.get("address", entry.get("Address", ""))
        if not addr or len(str(addr).strip()) < 10:
            continue

        addr_lower = str(addr).strip().lower()
        entry_json = json.dumps(entry)

        # Redis: rmi:label:ofac:{address}
        await storage.redis_set(f"rmi:label:ofac:{addr_lower}", entry_json)
        await storage.redis_sadd("rmi:labels:ofac", addr_lower)

        # ClickHouse row
        name = ""
        if isinstance(entry, dict):
            name = entry.get("name", entry.get("Name", entry.get("entity", "")))
        ch_rows.append(
            (
                addr_lower,
                "",
                str(name) if name else "OFAC Sanctioned",
                "sanctioned",
                "",
                "ofac",
                1,
                datetime.now(UTC),
            )
        )
        count += 1

    # Batch insert to ClickHouse
    if ch_rows and storage._ch:
        try:
            storage._ch.execute(
                """INSERT INTO wallet_labels
                (address, chain_id, label_name, label_category, label_subtype,
                 source, is_sanctioned, loaded_at) VALUES""",
                ch_rows,
            )
        except Exception as e:
            logger.warning(f"ClickHouse OFAC batch insert failed: {e}")

    await storage.redis_set("rmi:labels:ofac:count", str(count))
    logger.info(f"Loaded {count} OFAC sanctions")
    return count


# ── CSV loading ─────────────────────────────────────────────────────────


async def _load_csv_labels(
    storage,
    path: str,
    chain: str,
    addr_col: str,
    label_cols: dict[str, str],
    source_name: str = "",
) -> int:
    """
    Load a CSV of labeled addresses into Redis + ClickHouse via WalletStorage.
    Returns count of addresses loaded.
    """
    if not os.path.exists(path):
        logger.warning(f"CSV not found: {path}")
        return 0

    src = source_name or os.path.basename(path)

    # Skip if source already has labels in ClickHouse (prevents re-import)
    if storage._ch:
        try:
            existing = storage._ch.execute(
                "SELECT count() FROM wallet_labels WHERE source = %(src)s", {"src": src}
            )
            if existing and existing[0][0] > 100:
                logger.info(
                    f"Source '{src}' already has {existing[0][0]:,} labels in CH - skipping re-import"
                )
                return existing[0][0]
        except Exception:
            pass  # CH unavailable - import anyway for Redis

    count = 0
    ch_rows = []
    redis_pipe_ops = []  # Collect for batch Redis ops

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # Normalize headers to lowercase for case-insensitive matching
            if reader.fieldnames:
                reader.fieldnames = [h.lower().strip() for h in reader.fieldnames]
            for row in reader:
                addr = row.get(addr_col, "").strip().lower()
                if not addr or len(addr) < 10:
                    continue

                # Build label data dict
                label = {"chain": chain, "address": addr, "source": src}
                for key, col in label_cols.items():
                    val = row.get(col, "").strip()
                    if val:
                        label[key] = val

                # Also pick up any extra columns we recognise
                for extra in ("name", "label", "label_type", "label_subtype"):
                    if extra not in label:
                        val = row.get(extra, "").strip()
                        if val:
                            label[extra] = val

                label_json = json.dumps(label)

                # Redis: rmi:label:{chain}:{address} (matching wallet_label_loader format)
                redis_key = f"rmi:label:{chain}:{addr}"
                redis_pipe_ops.append((redis_key, label_json, addr))

                # ClickHouse row
                name = label.get(
                    "address_name",
                    label.get("dapp_name", label.get("protocol_name", label.get("name", ""))),
                )
                category = label.get("label_type", label.get("label", ""))
                subtype = label.get("label_subtype", "")
                ch_rows.append(
                    (
                        addr,
                        chain,
                        str(name) if name else "",
                        str(category) if category else "",
                        str(subtype) if subtype else "",
                        src,
                        0,
                        datetime.now(UTC),
                    )
                )

                count += 1

                # Flush in batches of 5000
                if count % 5000 == 0:
                    await _flush_batch(storage, redis_pipe_ops, ch_rows, chain)
                    redis_pipe_ops = []
                    ch_rows = []

    except Exception as e:
        logger.warning(f"Failed to load CSV {path}: {e}")

    # Flush remaining
    if redis_pipe_ops or ch_rows:
        await _flush_batch(storage, redis_pipe_ops, ch_rows, chain)

    logger.info(f"Loaded {count} labels from {src} (chain={chain})")
    return count


async def _flush_batch(
    storage,
    redis_ops: list[tuple[str, str, str]],
    ch_rows: list[tuple],
    chain: str,
):
    """Flush a batch of Redis writes and ClickHouse inserts."""
    # Batch Redis writes
    if storage._redis and redis_ops:
        try:
            async with storage._redis.pipeline() as pipe:
                for redis_key, label_json, addr in redis_ops:
                    pipe.set(redis_key, label_json)
                    pipe.sadd(f"rmi:labels:{chain}", addr)
                await pipe.execute()
        except Exception as e:
            # Fallback: individual writes
            logger.debug(f"Redis pipeline failed, falling back: {e}")
            for redis_key, label_json, addr in redis_ops:
                await storage.redis_set(redis_key, label_json)
                await storage.redis_sadd(f"rmi:labels:{chain}", addr)

    # Batch ClickHouse insert
    if ch_rows and storage._ch:
        try:
            storage._ch.execute(
                """INSERT INTO wallet_labels
                (address, chain_id, label_name, label_category, label_subtype,
                 source, is_sanctioned, loaded_at) VALUES""",
                ch_rows,
            )
        except Exception as e:
            logger.warning(f"ClickHouse batch insert failed: {e}")


# ── Etherscan combined labels ──────────────────────────────────────────


async def _load_etherscan_combined(storage) -> int:
    """
    Load etherscan_combined_labels.csv.
    Columns: address, name, label, chain, source
    """
    if not os.path.exists(ETHERSCAN_COMBINED):
        logger.warning(f"Etherscan combined labels not found: {ETHERSCAN_COMBINED}")
        return 0

    count = 0
    ch_rows = []
    redis_ops = []

    try:
        with open(ETHERSCAN_COMBINED, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row.get("address", "").strip().lower()
                if not addr or len(addr) < 10:
                    continue

                chain_raw = row.get("chain", "ethereum").strip().lower()
                # Normalize chain names
                chain = _normalize_chain(chain_raw)

                name = row.get("name", "").strip()
                label = row.get("label", "").strip()
                row.get("source", "etherscan").strip()

                label_data = {
                    "chain": chain,
                    "address": addr,
                    "address_name": name,
                    "label_type": label,
                    "source": "etherscan_combined",
                }
                label_json = json.dumps(label_data)

                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))

                ch_rows.append(
                    (
                        addr,
                        chain,
                        name,
                        label,
                        "",
                        "etherscan_combined",
                        0,
                        datetime.now(UTC),
                    )
                )

                count += 1

                if count % 5000 == 0:
                    await _flush_batch(storage, redis_ops, ch_rows, chain)
                    redis_ops = []
                    ch_rows = []

    except Exception as e:
        logger.warning(f"Failed to load etherscan combined: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "ethereum")

    logger.info(f"Loaded {count} etherscan combined labels")
    return count


# ── Etherscan phishing/hack labels ─────────────────────────────────────


async def _load_etherscan_phish_hack(storage) -> int:
    """
    Load etherscan_phish_hack.csv.
    Columns: address, name_tag, label_type, chain, balance, txn_count, source
    """
    if not os.path.exists(ETHERSCAN_PHISH_HACK):
        logger.warning(f"Etherscan phish/hack not found: {ETHERSCAN_PHISH_HACK}")
        return 0

    count = 0
    ch_rows = []
    redis_ops = []

    try:
        with open(ETHERSCAN_PHISH_HACK, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row.get("address", "").strip().lower()
                if not addr or len(addr) < 10:
                    continue

                chain_raw = row.get("chain", "ethereum").strip().lower()
                chain = _normalize_chain(chain_raw)

                name_tag = row.get("name_tag", "").strip()
                label_type = row.get("label_type", "").strip()
                balance = row.get("balance", "").strip()
                txn_count = row.get("txn_count", "").strip()

                label_data = {
                    "chain": chain,
                    "address": addr,
                    "address_name": name_tag,
                    "label_type": label_type,
                    "source": "etherscan_phish_hack",
                }
                if balance:
                    label_data["balance"] = balance
                if txn_count:
                    label_data["txn_count"] = txn_count

                label_json = json.dumps(label_data)
                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))

                # These are phishing/hack labels - also store in scam_addresses
                is_scam = 1 if label_type.lower() in ("scam", "phishing", "hack") else 0

                ch_rows.append(
                    (
                        addr,
                        chain,
                        name_tag,
                        label_type,
                        "",
                        "etherscan_phish_hack",
                        is_scam,
                        datetime.now(UTC),
                    )
                )

                # Also add to the scam_addresses ClickHouse table
                if is_scam and storage._ch:
                    with contextlib.suppress(Exception):
                        storage._ch.execute(
                            """INSERT INTO scam_addresses
                            (address, chain_id, source, threat_type, confidence,
                             first_seen_at, evidence) VALUES""",
                            [
                                (
                                    addr,
                                    chain,
                                    "etherscan_phish_hack",
                                    label_type,
                                    0.95,
                                    datetime.now(UTC),
                                    json.dumps({"name_tag": name_tag, "balance": balance}),
                                )
                            ],
                        )

                count += 1

    except Exception as e:
        logger.warning(f"Failed to load etherscan phish/hack: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "ethereum")

    logger.info(f"Loaded {count} etherscan phish/hack labels")
    return count


# ── BSC address tags ────────────────────────────────────────────────────


async def _ensure_bsc_labels() -> bool:
    """Download BSC detail CSV if not present."""
    dest = os.path.join(LABELS_DIR, "2024-01-31-detail.csv")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        logger.info(f"BSC detail labels already present ({os.path.getsize(dest)} bytes)")
        return True
    url = f"{BSC_LABELS_REPO}/2024-01-31-detail.csv"
    return await _download_file(url, dest)


async def _load_bsc_detail(storage) -> int:
    """Load BSC address tag detail CSV. 15K BSC addresses with entity labels."""
    path = os.path.join(LABELS_DIR, "2024-01-31-detail.csv")
    if not os.path.exists(path):
        logger.warning(f"BSC detail CSV not found: {path}")
        return 0

    count = 0
    ch_rows = []
    redis_ops = []

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row.get("address", "").strip().lower()
                if not addr or len(addr) < 10:
                    continue

                label_name = row.get("label_name", "").strip()
                name_tag = row.get("name_tag", "").strip()
                addr_type = row.get("address_type", "").strip()
                chain_code = row.get("chain_code", "BSC").strip().lower()
                chain = _normalize_chain(chain_code)

                category = (
                    "exchange"
                    if "exchange" in (label_name + name_tag).lower()
                    else "defi"
                    if any(
                        w in (label_name + name_tag).lower()
                        for w in ("protocol", "swap", "defi", "lend")
                    )
                    else "bridge"
                    if "bridge" in (label_name + name_tag).lower()
                    else "wallet"
                )

                label_data = {
                    "chain": chain,
                    "address": addr,
                    "address_name": label_name,
                    "label_type": name_tag or label_name,
                    "label_subtype": addr_type,
                    "source": "bsc_detail",
                }
                label_json = json.dumps(label_data)

                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))

                ch_rows.append(
                    (
                        addr,
                        chain,
                        name_tag or label_name,
                        category,
                        addr_type,
                        "bsc_detail",
                        0,
                        datetime.now(UTC),
                    )
                )

                count += 1
                if count % 5000 == 0:
                    await _flush_batch(storage, redis_ops, ch_rows, chain)
                    redis_ops = []
                    ch_rows = []

    except Exception as e:
        logger.warning(f"Failed to load BSC detail: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "bsc")

    logger.info(f"Loaded {count} BSC detail labels")
    return count


# ── Exchange cluster labels ─────────────────────────────────────────────


async def _ensure_exchange_cluster_labels() -> bool:
    """Download exchange cluster CSV."""
    dest = os.path.join(LABELS_DIR, "exchanges_cluster.csv")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    url = f"{ETH_CLUSTER_LABELS_REPO}/exchanges.csv"
    return await _download_file(url, dest)


async def _load_exchange_clusters(storage) -> int:
    """Load exchange cluster entity labels. ~200 exchange addresses."""
    path = os.path.join(LABELS_DIR, "exchanges_cluster.csv")
    # Also support direct-fetched path
    if not os.path.exists(path):
        path = os.path.join(LABELS_DIR, "exchanges.csv")
    if not os.path.exists(path):
        logger.warning("Exchange cluster CSV not found")
        return 0

    count = 0
    ch_rows = []
    redis_ops = []

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row.get("address", "").strip().lower()
                if not addr or len(addr) < 10:
                    continue

                name = row.get("name", "").strip()
                acct_type = row.get("accountType", "").strip()
                entity_type = row.get("type", "").strip()
                chain = "ethereum"

                label_data = {
                    "chain": chain,
                    "address": addr,
                    "address_name": name,
                    "label_type": entity_type,
                    "label_subtype": acct_type,
                    "source": "exchange_cluster",
                }
                label_json = json.dumps(label_data)

                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))

                ch_rows.append(
                    (
                        addr,
                        chain,
                        name,
                        entity_type.lower(),
                        acct_type,
                        "exchange_cluster",
                        0,
                        datetime.now(UTC),
                    )
                )

                count += 1

    except Exception as e:
        logger.warning(f"Failed to load exchange clusters: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "ethereum")

    logger.info(f"Loaded {count} exchange cluster labels")
    return count


# ── Contract name labels (name_dict.txt) ────────────────────────────────


async def _ensure_contract_name_labels() -> bool:
    """Download contract name dict."""
    dest = os.path.join(LABELS_DIR, "name_dict.txt")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    url = f"{ETH_CONTRACT_LABELS_REPO}/name_dict.txt"
    return await _download_file(url, dest)


async def _load_contract_names(storage) -> int:
    """Load contract name labels from name_dict.txt. ~900 Ethereum contracts."""
    path = os.path.join(LABELS_DIR, "name_dict.txt")
    if not os.path.exists(path):
        logger.warning("Contract name dict not found")
        return 0

    count = 0
    ch_rows = []
    redis_ops = []

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Format: "0xADDRESS - Name"
                parts = line.split(" - ", 1)
                if len(parts) < 2:
                    continue

                addr = parts[0].strip().lower()
                name = parts[1].strip()

                if not addr.startswith("0x") or len(addr) < 10:
                    continue

                chain = "ethereum"

                label_data = {
                    "chain": chain,
                    "address": addr,
                    "address_name": name,
                    "label_type": "contract",
                    "source": "contract_name_labels",
                }
                label_json = json.dumps(label_data)

                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))

                ch_rows.append(
                    (
                        addr,
                        chain,
                        name,
                        "contract",
                        "",
                        "contract_name_labels",
                        0,
                        datetime.now(UTC),
                    )
                )

                count += 1

    except Exception as e:
        logger.warning(f"Failed to load contract names: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "ethereum")

    logger.info(f"Loaded {count} contract name labels")
    return count


# ── Exchange/DEX wallet labels ──────────────────────────────────────────

_EXCHANGE_WALLET_FILES = [
    ("exchange.csv", "exchange_wallets", "ethereum"),
    ("dex.csv", "dex_wallets", "ethereum"),
    ("icowallets.csv", "ico_wallets", "ethereum"),
    ("mining1.csv", "mining_wallets_part1", "ethereum"),
    ("mining2.csv", "mining_wallets_part2", "ethereum"),
    ("walletapp.csv", "walletapp_wallets", "ethereum"),
]


async def _ensure_exchange_wallet_labels() -> dict[str, int]:
    """Download all exchange/DeFi wallet label CSVs."""
    os.makedirs(LABELS_DIR, exist_ok=True)
    results = {}
    for filename, _, _ in _EXCHANGE_WALLET_FILES:
        dest = os.path.join(LABELS_DIR, filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            results[filename] = os.path.getsize(dest)
        else:
            url = f"{ETH_EXCHANGE_LABELS_REPO}/{filename}"
            ok = await _download_file(url, dest)
            results[filename] = os.path.getsize(dest) if ok else 0
    return results


async def _load_exchange_wallets(storage) -> int:
    """Load exchange/dex/ico/mining wallet labels. ~350 labeled wallets."""
    # Files that have NO headers (address,entity,balance,txcount format)
    HEADERLESS_FILES = {"exchange.csv", "mining1.csv", "mining2.csv"}

    total = 0
    for filename, _, chain in _EXCHANGE_WALLET_FILES:
        path = os.path.join(LABELS_DIR, filename)
        if not os.path.exists(path):
            logger.warning(f"Exchange wallet CSV not found: {filename}")
            continue

        count = 0
        ch_rows = []
        redis_ops = []

        try:
            if filename in HEADERLESS_FILES:
                # Headerless CSV: address,entity,balance,txcount
                with open(path, encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if len(row) < 2:
                            continue
                        addr = row[0].strip().lower()
                        entity = row[1].strip() if len(row) > 1 else ""
                        balance = row[2].strip().strip('"') if len(row) > 2 else ""
                        txcount = row[3].strip().strip('"') if len(row) > 3 else ""

                        if not addr or len(addr) < 10:
                            continue

                        # Infer category
                        cat = "exchange"
                        if "mining" in filename:
                            cat = "mining"

                        label_data = {
                            "chain": chain,
                            "address": addr,
                            "address_name": entity,
                            "label_type": cat,
                            "source": "exchange_wallets",
                        }
                        if balance:
                            label_data["balance"] = balance
                        if txcount:
                            label_data["txn_count"] = txcount

                        label_json = json.dumps(label_data)
                        redis_key = f"rmi:label:{chain}:{addr}"
                        redis_ops.append((redis_key, label_json, addr))
                        ch_rows.append(
                            (
                                addr,
                                chain,
                                entity,
                                cat,
                                "",
                                "exchange_wallets",
                                0,
                                datetime.now(UTC),
                            )
                        )
                        count += 1
            else:
                # Standard CSVs with headers
                with open(path, newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        addr = row.get("address", "") or row.get("from_address", "")
                        addr = addr.strip().lower()
                        if not addr or len(addr) < 10:
                            continue

                        entity = row.get("entity", row.get("name", "")).strip()
                        balance = row.get("balance", "")
                        txcount = row.get("txcount", row.get("txn_count", ""))

                        cat = "exchange"
                        if "dex" in filename:
                            cat = "defi"
                        elif "ico" in filename:
                            cat = "ico"
                        elif "walletapp" in filename:
                            cat = "wallet"

                        label_data = {
                            "chain": chain,
                            "address": addr,
                            "address_name": entity,
                            "label_type": cat,
                            "source": "exchange_wallets",
                        }
                        if balance:
                            label_data["balance"] = balance
                        if txcount:
                            label_data["txn_count"] = txcount

                        label_json = json.dumps(label_data)
                        redis_key = f"rmi:label:{chain}:{addr}"
                        redis_ops.append((redis_key, label_json, addr))
                        ch_rows.append(
                            (
                                addr,
                                chain,
                                entity,
                                cat,
                                "",
                                "exchange_wallets",
                                0,
                                datetime.now(UTC),
                            )
                        )
                        count += 1

        except Exception as e:
            logger.warning(f"Failed to load {filename}: {e}")
            continue

        if redis_ops or ch_rows:
            await _flush_batch(storage, redis_ops, ch_rows, chain)

        logger.info(f"Loaded {count} labels from {filename}")
        total += count

    logger.info(f"Loaded {total} total exchange wallet labels")
    return total


# ── Chain normalization ─────────────────────────────────────────────────

_CHAIN_ALIASES = {
    "eth": "ethereum",
    "eth_mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "bnb_chain": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "base": "base",
    "sol": "solana",
    "solana": "solana",
}


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to canonical form."""
    return _CHAIN_ALIASES.get(chain.lower().strip(), chain.lower().strip()) or "ethereum"


# ── ClickHouse schema ───────────────────────────────────────────────────


async def _ensure_wallet_labels_table(storage):
    """Create the wallet_labels table in ClickHouse if it doesn't exist."""
    if not storage._ch:
        return
    try:
        storage._ch.execute("CREATE DATABASE IF NOT EXISTS wallet_memory")
        storage._ch.execute(WALLET_LABELS_DDL)
        logger.info("wallet_labels table ensured in ClickHouse")
    except Exception as e:
        logger.warning(f"Failed to create wallet_labels table: {e}")


# ── Main entry point ────────────────────────────────────────────────────

_loading = False
_last_load_counts: dict[str, int] = {}


async def load_all_static_labels() -> dict[str, int]:
    """
    Load all static wallet label datasets on boot.

    Sources:
      1. Solana CEX labels (crypto-wallet-address-labels GitHub)
      2. Solana DApp labels
      3. Solana DeFi labels
      4. Etherscan combined labels (50K rows)
      5. Etherscan phishing/hack labels (111 rows)
      6. OFAC crypto sanctions
      7. BSC address tags (15K rows)
      8. Exchange cluster labels (~200)
      9. Contract name labels (~900)
     10. Exchange/DEX/ICO/Mining wallet labels (~350)

    Returns counts per source.
    """
    global _loading, _last_load_counts

    if _loading:
        logger.info("Label import already in progress - skipping")
        return _last_load_counts

    _loading = True
    start = time.time()
    counts: dict[str, int] = {}

    try:
        from .storage import get_storage

        storage = get_storage()

        # Ensure connections
        await storage._ensure_connections()
        logger.info(
            f"Storage connections: redis={'ok' if storage._redis else 'no'}, clickhouse={'ok' if storage._ch else 'no'}"
        )

        # Ensure ClickHouse table exists
        await _ensure_wallet_labels_table(storage)

        # 1. Download Solana label CSVs if needed
        await _ensure_solana_labels()

        # 2. Load Solana CSVs
        for filename, chain, addr_col, label_cols in SOLANA_CSV_SPECS:
            path = os.path.join(LABELS_DIR, filename)
            key = filename.replace(".csv", "").replace("_labels", "")
            if os.path.exists(path):
                c = await _load_csv_labels(
                    storage, path, chain, addr_col, label_cols, source_name=key
                )
                counts[key] = c
            else:
                counts[key] = 0
                logger.warning(f"Solana labels missing: {filename}")

        # 2b. Load additional ETH phishing/scam CSVs from wallet-labels dir
        for filename, chain, addr_col, label_cols in ETH_CSV_SPECS:
            path = os.path.join(LABELS_DIR, filename)
            key = filename.replace(".csv", "").replace("_labels", "")
            if os.path.exists(path):
                c = await _load_csv_labels(
                    storage, path, chain, addr_col, label_cols, source_name=key
                )
                counts[key] = c
            else:
                counts[key] = 0

        # 3. Load Etherscan combined labels
        counts["etherscan_combined"] = await _load_etherscan_combined(storage)

        # 4. Load Etherscan phishing/hack labels
        counts["etherscan_phish_hack"] = await _load_etherscan_phish_hack(storage)

        # 5. Load OFAC sanctions
        counts["ofac"] = await _load_ofac_sanctions(storage)

        # === NEW SOURCES ===

        # 6. Download and load BSC address tags
        await _ensure_bsc_labels()
        counts["bsc_detail"] = await _load_bsc_detail(storage)

        # 7. Download and load exchange cluster labels
        await _ensure_exchange_cluster_labels()
        counts["exchange_cluster"] = await _load_exchange_clusters(storage)

        # 8. Download and load contract name labels
        await _ensure_contract_name_labels()
        counts["contract_names"] = await _load_contract_names(storage)

        # 9. Download and load exchange/DEX wallet labels
        await _ensure_exchange_wallet_labels()
        counts["exchange_wallets"] = await _load_exchange_wallets(storage)

        # Store aggregate counts in Redis
        total = sum(counts.values())
        counts["total"] = total
        await storage.redis_set("rmi:labels:counts", json.dumps(counts))

        elapsed = time.time() - start
        logger.info(f"Static label import complete: {total} labels in {elapsed:.1f}s - {counts}")

    except Exception as e:
        logger.error(f"Static label import failed: {e}", exc_info=True)
        counts["error"] = str(e)
    finally:
        _loading = False

    _last_load_counts = counts
    return counts


def get_last_load_counts() -> dict[str, int]:
    """Return counts from the most recent label import."""
    return _last_load_counts


def is_loading() -> bool:
    """Check if a label import is currently in progress."""
    return _loading


# ── Clean Data Loader (from label quality fixer) ─────────────────────

CLEAN_DIR = os.path.join(DATA_DIR, "wallet-labels-clean")


async def load_clean_labels(storage) -> dict:
    """Load pre-cleaned, deduplicated labels from the quality fixer.
    Returns stats dict with counts."""
    import csv

    stats = {"ethereum": 0, "solana": 0, "errors": 0}

    for chain in ["ethereum", "solana"]:
        path = os.path.join(CLEAN_DIR, f"wallet_labels_{chain}.csv")
        if not os.path.exists(path):
            logger.warning(f"Clean labels file not found: {path}")
            continue

        labels = []
        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                labels.append(
                    {
                        "address": row["address"],
                        "chain": row["chain"],
                        "source": row.get("source", "clean_import"),
                        "name": row.get("name", ""),
                        "label_type": row.get("label_type", ""),
                        "label_subtype": row.get("label_subtype", ""),
                        "project": row.get("project", ""),
                        "entity_type": row.get("entity_type", "unknown"),
                    }
                )

        if labels and storage:
            try:
                await storage.bulk_import(labels, source=f"clean_{chain}")
                stats[chain] = len(labels)
                logger.info(f"Loaded {len(labels):,} clean {chain} labels")
            except Exception as e:
                logger.error(f"Failed to import clean {chain} labels: {e}")
                stats["errors"] += 1

    logger.info(f"Clean label import complete: {stats}")
    return stats
