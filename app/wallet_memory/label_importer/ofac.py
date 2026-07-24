"""OFAC sanctions loader."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from app.core.logging import get_logger

from .config import LABELS_DIR, OFAC_URL
from .download import _download_json

logger = get_logger("wallet_memory.label_importer")

async def _load_ofac_sanctions(storage) -> int:
    """Load OFAC crypto-sanctions-list into storage.
    Tries local file first, then downloads from GitHub.
    """
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

    try:
        os.makedirs(LABELS_DIR, exist_ok=True)
        with open(local_ofac, "w", encoding="utf-8") as f:
            json.dump(sanctions, f)
    except (ConnectionError, OSError, TimeoutError):
        pass

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

        await storage.redis_set(f"rmi:label:ofac:{addr_lower}", entry_json)
        await storage.redis_sadd("rmi:labels:ofac", addr_lower)

        name = ""
        if isinstance(entry, dict):
            name = entry.get("name", entry.get("Name", entry.get("entity", "")))
        ch_rows.append((
            addr_lower, "",
            str(name) if name else "OFAC Sanctioned",
            "sanctioned", "", "ofac", 1, datetime.now(UTC),
        ))
        count += 1

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
