#!/usr/bin/env python3
"""
Ingest CryptoScamDB entries into RAG known_scams collection.
API: https://api.cryptoscamdb.org/v1/scams
"""

import asyncio
import hashlib
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_cryptoscamdb")

API_URLS = [
    "https://api.cryptoscamdb.org/v1/scams",
    "https://api.cryptoscamdb.org/v1/all",
]
RAG_API = "http://localhost:8000/api/v1/rag/ingest"
COLLECTION = "known_scams"


def make_doc_id(address: str, source: str) -> str:
    return hashlib.sha256(f"known_scams:{address.lower()}:{source}".encode()).hexdigest()[:16]


async def ingest_via_api(content: str, metadata: dict) -> bool:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                RAG_API, json={"collection": COLLECTION, "content": content, "metadata": metadata}
            )
            return resp.status_code == 200
    except Exception as e:
        logger.debug(f"API ingest failed: {e}")
        return False


async def main():
    import httpx

    # Try each API URL
    data = None
    for url in API_URLS:
        try:
            logger.info(f"Fetching from {url}...")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"Got response from {url}")
                    break
                else:
                    logger.warning(f"{url} returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")

    if not data:
        logger.error("All CryptoScamDB API endpoints failed")
        return

    # Extract scam entries (API returns nested structure)
    scams = []
    if isinstance(data, dict):
        scams = data.get("result", data.get("scams", data.get("data", [])))
    elif isinstance(data, list):
        scams = data

    if not scams:
        logger.error(
            f"No scam entries found in response. Keys: {list(data.keys()) if isinstance(data, dict) else 'list'}"
        )
        return

    logger.info(f"Found {len(scams)} scam entries")

    ingested = 0
    errors = 0

    for i, scam in enumerate(scams):
        if not isinstance(scam, dict):
            continue

        name = scam.get("name", "")
        category = scam.get("category", "unknown")
        status = scam.get("status", "unknown")
        description = scam.get("description", "")
        url = scam.get("url", "")
        addresses = scam.get("addresses", [])

        if not addresses:
            # Create one doc for URL-based scam
            content = f"CryptoScamDB entry: {name}. Category: {category}. Status: {status}. URL: {url}. Description: {description}"
            metadata = {
                "source": "cryptoscamdb",
                "category": category,
                "status": status,
                "name": name,
                "url": url,
                "severity": "high" if status == "Active" else "medium",
            }
            make_doc_id(name or url or str(i), "cryptoscamdb")
            if await ingest_via_api(content, metadata):
                ingested += 1
            else:
                errors += 1
        else:
            # Create one doc per blockchain address
            for addr in addresses:
                if not addr or len(addr) < 10:
                    continue
                content = f"CryptoScamDB flagged address: {addr}. Project: {name}. Category: {category}. Status: {status}. Description: {description}"
                metadata = {
                    "source": "cryptoscamdb",
                    "address": addr.lower(),
                    "category": category,
                    "status": status,
                    "name": name,
                    "severity": "high" if status == "Active" else "medium",
                }
                if await ingest_via_api(content, metadata):
                    ingested += 1
                else:
                    errors += 1

        if (i + 1) % 100 == 0:
            logger.info(f"Progress: {i + 1}/{len(scams)} | ingested: {ingested}, errors: {errors}")
            await asyncio.sleep(0.1)  # Yield to event loop

    logger.info(f"COMPLETE: ingested={ingested}, errors={errors}")


if __name__ == "__main__":
    asyncio.run(main())
