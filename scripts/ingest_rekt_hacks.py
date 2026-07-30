#!/usr/bin/env python3
"""
Ingest rekt.news hack data into RAG known_scams collection.

Usage:
  # Via HTTP API (from host or any networked client):
  python3 scripts/ingest_rekt_hacks.py --api

  # Direct Python import (run inside Docker container):
  docker exec rmi-backend python3 scripts/ingest_rekt_hacks.py --direct

  # Dry run (print what would be ingested):
  python3 scripts/ingest_rekt_hacks.py --dry-run
"""

import argparse
import asyncio
import json
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "rekt_hacks.json")
API_URL = os.getenv("RAG_API_URL", "http://localhost:8000/api/v1/rag/ingest")
COLLECTION = "known_scams"


def load_hacks():
    with open(DATA_FILE) as f:
        return json.load(f)


def build_doc_text(hack):
    """Build a rich text document for semantic search."""
    parts = []
    parts.append(f"Project: {hack.get('name')}")
    if hack.get("date"):
        parts.append(f"Date: {hack['date']}")
    parts.append(f"Amount Lost: ${hack['amount_lost']:,.0f}")
    if hack.get("chain"):
        parts.append(f"Chain: {hack['chain']}")
    if hack.get("attack_type"):
        parts.append(f"Attack Type: {hack['attack_type']}")
    if hack.get("audit_status"):
        parts.append(f"Audit Status: {hack['audit_status']}")
    if hack.get("description"):
        parts.append(f"Description: {hack['description']}")
    if hack.get("tags"):
        parts.append(f"Tags: {', '.join(hack['tags'])}")
    if hack.get("url"):
        parts.append(f"URL: {hack['url']}")
    return "\n".join(parts)


def build_metadata(hack):
    """Build metadata dict for the RAG document."""
    meta = {
        "source": "rekt_news",
        "name": hack.get("name"),
        "amount_lost": hack["amount_lost"],  # Required by embed_scam_pattern
    }
    if hack.get("date"):
        meta["date"] = hack["date"]
    if hack.get("chain"):
        meta["chain"] = hack["chain"]
    if hack.get("attack_type"):
        meta["attack_type"] = hack["attack_type"]
    if hack.get("audit_status"):
        meta["audit_status"] = hack["audit_status"]
    if hack.get("url"):
        meta["url"] = hack["url"]
    if hack.get("slug"):
        meta["slug"] = hack["slug"]
    if hack.get("tags"):
        meta["tags"] = hack["tags"]

    # Severity classification
    amt = hack["amount_lost"]
    if amt >= 100_000_000:
        meta["severity"] = "critical"
    elif amt >= 10_000_000:
        meta["severity"] = "high"
    elif amt >= 1_000_000:
        meta["severity"] = "medium"
    else:
        meta["severity"] = "low"

    return meta


def make_doc_id(hack):
    """Generate deterministic doc ID from slug."""
    slug = hack.get("slug") or hack.get("name").lower().replace(" ", "-").replace("/", "-")
    # Clean slug
    slug = "".join(c for c in slug if c.isalnum() or c in "-_").strip("-")
    return f"rekt-{slug}"


# ─────────────────────────────────────────────────────────────
# Method 1: HTTP API
# ─────────────────────────────────────────────────────────────
async def ingest_via_api(hacks, dry_run=False):
    import urllib.request

    success = 0
    failed = 0

    for i, hack in enumerate(hacks):
        doc_id = make_doc_id(hack)
        text = build_doc_text(hack)
        metadata = build_metadata(hack)

        payload = json.dumps(
            {
                "collection": COLLECTION,
                "content": text,
                "metadata": metadata,
                "doc_id": doc_id,
            }
        ).encode("utf-8")

        if dry_run:
            if i < 3:
                print(f"[DRY RUN] Would ingest: {doc_id} | {hack.get('name')} | ${hack['amount_lost']:,.0f}")
            continue

        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            json.loads(resp.read())
            success += 1
            if (i + 1) % 20 == 0:
                print(f"  Progress: {i + 1}/{len(hacks)} ingested ({success} ok, {failed} fail)")
        except Exception as e:
            failed += 1
            print(f"  FAILED {doc_id}: {e}")

        # Rate limit: small delay between requests
        if (i + 1) % 10 == 0:
            time.sleep(0.5)

    return success, failed


# ─────────────────────────────────────────────────────────────
# Method 2: Direct Python import (run inside Docker)
# ─────────────────────────────────────────────────────────────
async def ingest_direct(hacks, dry_run=False):
    from app.rag_service import ingest_document

    success = 0
    failed = 0

    for i, hack in enumerate(hacks):
        doc_id = make_doc_id(hack)
        text = build_doc_text(hack)
        metadata = build_metadata(hack)

        if dry_run:
            if i < 3:
                print(f"[DRY RUN] Would ingest: {doc_id} | {hack.get('name')} | ${hack['amount_lost']:,.0f}")
            continue

        try:
            await ingest_document(
                collection=COLLECTION,
                content=text,
                metadata=metadata,
                doc_id=doc_id,
            )
            success += 1
            if (i + 1) % 20 == 0:
                print(f"  Progress: {i + 1}/{len(hacks)} ingested ({success} ok, {failed} fail)")
        except Exception as e:
            failed += 1
            print(f"  FAILED {doc_id}: {e}")

        # Yield control periodically
        if (i + 1) % 10 == 0:
            await asyncio.sleep(0.1)

    return success, failed


async def main():
    parser = argparse.ArgumentParser(description="Ingest rekt.news hacks into RAG")
    parser.add_argument("--api", action="store_true", help="Use HTTP API (default if running from host)")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Use direct Python import (run inside Docker container)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be ingested without actually ingesting",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of hacks to ingest (0 = all)")
    args = parser.parse_args()

    hacks = load_hacks()
    print(f"Loaded {len(hacks)} hacks from {DATA_FILE}")

    if args.limit > 0:
        hacks = hacks[: args.limit]
        print(f"Limiting to first {args.limit} hacks")

    # Default to API if no method specified
    if not args.direct:
        args.api = True

    if args.dry_run:
        print("\n=== DRY RUN ===")
        for hack in hacks[:5]:
            doc_id = make_doc_id(hack)
            text = build_doc_text(hack)
            metadata = build_metadata(hack)
            print(f"\n--- {doc_id} ---")
            print(f"Text preview: {text[:200]}...")
            print(f"Metadata: {json.dumps(metadata, indent=2)[:300]}...")
        if len(hacks) > 5:
            print(f"\n... and {len(hacks) - 5} more")
        return

    method = "HTTP API" if args.api else "Direct Python"
    print(f"\nIngesting {len(hacks)} hacks via {method}...")
    print(f"Collection: {COLLECTION}")
    print()

    if args.api:
        success, failed = await ingest_via_api(hacks)
    else:
        success, failed = await ingest_direct(hacks)

    print("\n=== DONE ===")
    print(f"Success: {success}")
    print(f"Failed: {failed}")
    print(f"Total: {len(hacks)}")


if __name__ == "__main__":
    asyncio.run(main())
