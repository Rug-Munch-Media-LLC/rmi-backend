"""Exchange cluster, contract name, and exchange wallet label loaders."""

import csv
import json
from datetime import UTC, datetime

from app.core.logging import get_logger
from .config import LABELS_DIR
from .loaders import _flush_batch

logger = get_logger("wallet_memory.label_importer")


async def _load_exchange_clusters(storage) -> int:
    """Load exchange cluster entity labels. ~200 exchange addresses."""
    path = os.path.join(LABELS_DIR, "exchanges_cluster.csv")
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
                    "chain": chain, "address": addr,
                    "address_name": name, "label_type": entity_type,
                    "label_subtype": acct_type, "source": "exchange_cluster",
                }
                label_json = json.dumps(label_data)
                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))
                ch_rows.append((addr, chain, name, entity_type.lower(), acct_type,
                                "exchange_cluster", 0, datetime.now(UTC)))
                count += 1
    except Exception as e:
        logger.warning(f"Failed to load exchange clusters: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "ethereum")

    logger.info(f"Loaded {count} exchange cluster labels")
    return count


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
                parts = line.split(" - ", 1)
                if len(parts) < 2:
                    continue
                addr = parts[0].strip().lower()
                name = parts[1].strip()
                if not addr.startswith("0x") or len(addr) < 10:
                    continue
                chain = "ethereum"
                label_data = {
                    "chain": chain, "address": addr,
                    "address_name": name, "label_type": "contract",
                    "source": "contract_name_labels",
                }
                label_json = json.dumps(label_data)
                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))
                ch_rows.append((addr, chain, name, "contract", "",
                                "contract_name_labels", 0, datetime.now(UTC)))
                count += 1
    except Exception as e:
        logger.warning(f"Failed to load contract names: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "ethereum")

    logger.info(f"Loaded {count} contract name labels")
    return count


async def _load_exchange_wallets(storage) -> int:
    """Load exchange/dex/ico/mining wallet labels. ~350 labeled wallets."""
    HEADERLESS_FILES = {"exchange.csv", "mining1.csv", "mining2.csv"}

    total = 0
    for filename, _, chain in [
        ("exchange.csv", "exchange_wallets", "ethereum"),
        ("dex.csv", "dex_wallets", "ethereum"),
        ("icowallets.csv", "ico_wallets", "ethereum"),
        ("mining1.csv", "mining_wallets_part1", "ethereum"),
        ("mining2.csv", "mining_wallets_part2", "ethereum"),
        ("walletapp.csv", "walletapp_wallets", "ethereum"),
    ]:
        path = os.path.join(LABELS_DIR, filename)
        if not os.path.exists(path):
            logger.warning(f"Exchange wallet CSV not found: {filename}")
            continue

        count = 0
        ch_rows = []
        redis_ops = []

        try:
            if filename in HEADERLESS_FILES:
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
                        cat = "mining" if "mining" in filename else "exchange"
                        label_data = {
                            "chain": chain, "address": addr,
                            "address_name": entity, "label_type": cat,
                            "source": "exchange_wallets",
                        }
                        if balance:
                            label_data["balance"] = balance
                        if txcount:
                            label_data["txn_count"] = txcount
                        label_json = json.dumps(label_data)
                        redis_key = f"rmi:label:{chain}:{addr}"
                        redis_ops.append((redis_key, label_json, addr))
                        ch_rows.append((addr, chain, entity, cat, "",
                                        "exchange_wallets", 0, datetime.now(UTC)))
                        count += 1
            else:
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
                            "chain": chain, "address": addr,
                            "address_name": entity, "label_type": cat,
                            "source": "exchange_wallets",
                        }
                        if balance:
                            label_data["balance"] = balance
                        if txcount:
                            label_data["txn_count"] = txcount
                        label_json = json.dumps(label_data)
                        redis_key = f"rmi:label:{chain}:{addr}"
                        redis_ops.append((redis_key, label_json, addr))
                        ch_rows.append((addr, chain, entity, cat, "",
                                        "exchange_wallets", 0, datetime.now(UTC)))
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
