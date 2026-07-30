#!/usr/bin/env python3
"""
Ingest ChainAbuse reports into RAG known_scams collection.
API: https://api.chainabuse.com/v1/reports
"""

import asyncio
import hashlib
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_chainabuse")

CHAINABUSE_API = "https://api.chainabuse.com/v1/reports"
RAG_API = "http://localhost:8000/api/v1/rag/ingest"
COLLECTION = "known_scams"


def make_doc_id(address: str) -> str:
    return hashlib.sha256(f"known_scams:{address.lower()}:chainabuse".encode()).hexdigest()[:16]


async def ingest_via_api(content: str, metadata: dict) -> bool:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(RAG_API, json={"collection": COLLECTION, "content": content, "metadata": metadata})
            return resp.status_code == 200
    except Exception as e:
        logger.debug(f"API ingest failed: {e}")
        return False


async def main():
    import httpx

    # Try multiple endpoints and pagination
    all_reports = []
    for page in range(5):  # Up to 5 pages of 100
        url = f"{CHAINABUSE_API}?limit=100&offset={page * 100}"
        try:
            logger.info(f"Fetching page {page + 1} from ChainAbuse...")
            async with httpx.AsyncClient(timeout=30, headers={"Accept": "application/json"}) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    reports = data if isinstance(data, list) else data.get("data", data.get("reports", []))
                    if not reports:
                        logger.info(f"No more reports at page {page + 1}")
                        break
                    all_reports.extend(reports)
                    logger.info(f"Got {len(reports)} reports (total: {len(all_reports)})")
                elif resp.status_code == 401:
                    logger.warning("ChainAbuse API requires authentication - trying public feed")
                    break
                else:
                    logger.warning(f"ChainAbuse returned {resp.status_code}")
                    break
        except Exception as e:
            logger.warning(f"ChainAbuse fetch failed: {e}")
            break

    if not all_reports:
        # Try alternative public endpoints
        alt_urls = [
            "https://api.chainabuse.com/v0/reports?limit=100",
            "https://api.chainabuse.com/reports?limit=100",
            "https://chainabuse.com/api/reports",
        ]
        for url in alt_urls:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        reports = data if isinstance(data, list) else data.get("data", data.get("reports", []))
                        if reports:
                            all_reports.extend(reports)
                            logger.info(f"Got {len(reports)} reports from alt endpoint")
                            break
            except Exception:
                pass

    if not all_reports:
        logger.warning("No ChainAbuse reports accessible via API. Ingestion skipped.")
        logger.info("ChainAbuse may require API key - visit https://chainabuse.com for access")
        return

    logger.info(f"Processing {len(all_reports)} ChainAbuse reports")

    ingested = 0
    errors = 0

    for i, report in enumerate(all_reports):
        if not isinstance(report, dict):
            continue

        address = report.get("address", report.get("targetAddress", ""))
        category = report.get("category", report.get("type", "unknown"))
        description = report.get("description", report.get("report", ""))
        status = report.get("status", "unknown")
        reporter = report.get("reporter", "")

        if not address:
            continue

        content = f"ChainAbuse report: {address}. Category: {category}. Status: {status}. Description: {description}. Reporter: {reporter}."
        metadata = {
            "source": "chainabuse",
            "address": address.lower(),
            "category": category,
            "status": status,
            "severity": "high",
        }

        if await ingest_via_api(content, metadata):
            ingested += 1
        else:
            errors += 1

        if (i + 1) % 50 == 0:
            logger.info(f"Progress: {i + 1}/{len(all_reports)} | ingested: {ingested}")

    logger.info(f"COMPLETE: ingested={ingested}, errors={errors}")


if __name__ == "__main__":
    asyncio.run(main())
