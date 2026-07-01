#!/usr/bin/env python3
"""
Ingest CryptoScamDB blacklist data into RAG known_scams collection.
Source: https://github.com/CryptoScamDB/blacklist
"""

import asyncio
import hashlib
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_cryptoscamdb_blacklist")

RAG_API = "http://localhost:8000/api/v1/rag/ingest"
COLLECTION = "known_scams"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def make_doc_id(address: str, source: str) -> str:
    return hashlib.sha256(f"known_scams:{address.lower()}:{source}".encode()).hexdigest()[:16]


async def ingest_via_api(content: str, metadata: dict) -> bool:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(RAG_API, json={"collection": COLLECTION, "content": content, "metadata": metadata})
            return resp.status_code == 200
    except Exception:
        return False


async def ingest_yaml():
    """Parse the YAML blacklist and ingest entries with addresses."""
    yaml_path = os.path.join(DATA_DIR, "cryptoscamdb_urls.yaml")
    if not os.path.exists(yaml_path):
        logger.error(f"YAML file not found: {yaml_path}")
        return {"ingested": 0, "no_address": 0}

    # Simple YAML parser (avoid pyyaml dependency for this structure)
    entries = []
    current = {}
    with open(yaml_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("- name:"):
                if current:
                    entries.append(current)
                current = {"name": stripped[7:].strip().strip('"')}
            elif stripped.startswith("  url:"):
                current["url"] = stripped[6:].strip().strip('"')
            elif stripped.startswith("  category:"):
                current["category"] = stripped[11:].strip().strip('"')
            elif stripped.startswith("  subcategory:"):
                current["subcategory"] = stripped[14:].strip().strip('"')
            elif stripped.startswith("  description:"):
                current["description"] = stripped[14:].strip().strip('"')
            elif stripped.startswith("  reporter:"):
                current["reporter"] = stripped[11:].strip().strip('"')
            elif stripped.startswith("    ETH:"):
                current["chain"] = "ethereum"
            elif stripped.startswith('    - "0x') or stripped.startswith("    - 0x"):
                addr = stripped.split('"')[-2] if '"' in stripped else stripped.split("-")[-1].strip()
                current.setdefault("addresses", []).append(addr)
            elif stripped.startswith("    BTC:"):
                current["chain"] = "bitcoin"
            elif stripped.startswith("    BSC:") or stripped.startswith("    BNB:"):
                current["chain"] = "bsc"
        if current:
            entries.append(current)

    logger.info(f"Parsed {len(entries)} entries from YAML")

    ingested = 0
    no_address = 0

    for i, entry in enumerate(entries):
        name = entry.get("name", "")
        category = entry.get("category", "unknown")
        subcategory = entry.get("subcategory", "")
        description = entry.get("description", "")
        url = entry.get("url", "")
        addresses = entry.get("addresses", [])

        if not addresses:
            # Ingest URL-based entry without blockchain address
            content = f"CryptoScamDB: {name}. Category: {category}/{subcategory}. URL: {url}. {description}"
            metadata = {
                "source": "cryptoscamdb",
                "category": category.lower(),
                "subcategory": subcategory.lower(),
                "name": name,
                "url": url,
                "severity": "high" if category == "Phishing" else "medium",
            }
            if await ingest_via_api(content, metadata):
                no_address += 1
        else:
            for addr in addresses:
                if not addr or len(addr) < 10:
                    continue
                chain = entry.get("chain", "ethereum")
                content = f"CryptoScamDB flagged address: {addr} on {chain}. Project: {name}. Category: {category}/{subcategory}. {description}"
                metadata = {
                    "source": "cryptoscamdb",
                    "address": addr.lower(),
                    "chain": chain,
                    "category": category.lower(),
                    "subcategory": subcategory.lower(),
                    "name": name,
                    "url": url,
                    "severity": "high" if category in ("Phishing", "Scam") else "medium",
                }
                if await ingest_via_api(content, metadata):
                    ingested += 1

        if (i + 1) % 500 == 0:
            logger.info(f"Progress: {i + 1}/{len(entries)} | addr_entries: {ingested}, url_only: {no_address}")
            await asyncio.sleep(0.1)

    logger.info(f"COMPLETE: addr_entries={ingested}, url_only={no_address}")
    return {"ingested": ingested, "no_address": no_address}


async def main():
    result = await ingest_yaml()
    logger.info(f"Final: {result}")


if __name__ == "__main__":
    asyncio.run(main())
