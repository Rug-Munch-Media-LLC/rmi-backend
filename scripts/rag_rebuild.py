#!/usr/bin/env python3
"""Fast RAG rebuild — run all ingestion sources + build FAISS indexes.
Single-pass, single client, proper error logging."""

import asyncio
import csv
import hashlib
import json
import logging
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rag_rebuild")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RAG_API = "http://localhost:8000/api/v1/rag/ingest"

SCAM_KEYWORDS = [
    "phish",
    "hack",
    "exploit",
    "scam",
    "drain",
    "attack",
    "heist",
    "malicious",
    "fraud",
    "rug",
    "compromis",
    "fake",
]


def doc_id(collection, key):
    return hashlib.sha256(f"{collection}:{key.lower()}".encode()).hexdigest()[:16]


async def run():
    total_ingested = 0
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=20)) as client:
        # ── 1. Etherscan Phish/Hack ──
        csv_path = os.path.join(DATA_DIR, "etherscan_phish_hack.csv")
        if os.path.exists(csv_path):
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            logger.info(f"1. Etherscan phish/hack: {len(rows)} addresses")
            ok = err = 0
            for i, row in enumerate(rows):
                addr = row.get("address", "").strip()
                if not addr:
                    continue
                payload = {
                    "collection": "known_scams",
                    "content": f"Etherscan {row.get('label_type', 'scam')} label: {addr} on {row.get('chain', 'Ethereum')}. Tag: {row.get('name_tag', '')}. Balance: {row.get('balance', '0')}. Txns: {row.get('txn_count', '0')}.",
                    "metadata": {
                        "address": addr.lower(),
                        "label_type": row.get("label_type", "scam"),
                        "name_tag": row.get("name_tag", ""),
                        "chain": (row.get("chain", "Ethereum") or "Ethereum").lower(),
                        "source": "etherscan_phish_hack",
                        "severity": "critical",
                    },
                }
                try:
                    resp = await client.post(RAG_API, json=payload)
                    if resp.status_code == 200:
                        ok += 1
                    else:
                        err += 1
                        if err <= 3:
                            logger.warning(f"  Ingest error {resp.status_code}: {resp.text[:200]}")
                except Exception as e:
                    err += 1
                    if err <= 3:
                        logger.warning(f"  Connection error: {e}")
                if (i + 1) % 25 == 0:
                    logger.info(f"  Phish/hack: {i + 1}/{len(rows)} | ok={ok} err={err}")
                    await asyncio.sleep(0.1)
            total_ingested += ok
            logger.info(f"  Done: {ok} ingested, {err} errors")

        # ── 2. Etherscan Combined Labels ──
        csv_path = os.path.join(DATA_DIR, "etherscan_combined_labels.csv")
        if os.path.exists(csv_path):
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            scam_rows = [r for r in rows if any(kw in (r.get("label", "") or "").lower() for kw in SCAM_KEYWORDS)]
            wallet_rows = [r for r in rows if not any(kw in (r.get("label", "") or "").lower() for kw in SCAM_KEYWORDS)]
            logger.info(f"2. Etherscan combined: {len(rows)} total → {len(scam_rows)} scam, {len(wallet_rows)} wallets")

            ok_scam = ok_wallet = err = 0
            # Scam addresses first
            for i, row in enumerate(scam_rows):
                addr = row.get("address", "").strip()
                if not addr:
                    continue
                payload = {
                    "collection": "known_scams",
                    "content": f"Etherscan scam label: {addr} on {row.get('chain', 'Ethereum')}. Label: {row.get('label', '')}. Category: {row.get('category', '')}.",
                    "metadata": {
                        "address": addr.lower(),
                        "label": row.get("label", ""),
                        "category": row.get("category", ""),
                        "chain": (row.get("chain", "Ethereum") or "Ethereum").lower(),
                        "source": "etherscan_combined",
                        "severity": "high",
                    },
                }
                try:
                    resp = await client.post(RAG_API, json=payload)
                    if resp.status_code == 200:
                        ok_scam += 1
                    else:
                        err += 1
                except:
                    err += 1
                if (i + 1) % 100 == 0:
                    logger.info(f"  Scam: {i + 1}/{len(scam_rows)} | ok={ok_scam}")
                    await asyncio.sleep(0.1)

            # Wallet profiles (batch for speed, skip if 0 scam found — data issue)
            batch_size = 50
            for i in range(0, min(len(wallet_rows), 5000), batch_size):
                batch = wallet_rows[i : i + batch_size]
                content_parts = []
                for row in batch:
                    content_parts.append(
                        f"{row.get('address', '')}: {row.get('label', '')} on {row.get('chain', 'Ethereum')}"
                    )
                payload = {
                    "collection": "wallet_profiles",
                    "content": "Etherscan wallet labels batch: " + " | ".join(content_parts),
                    "metadata": {
                        "source": "etherscan_combined",
                        "chain": "multi",
                        "count": len(batch),
                        "label_type": "wallet_profile",
                    },
                }
                try:
                    resp = await client.post(RAG_API, json=payload)
                    if resp.status_code == 200:
                        ok_wallet += len(batch)
                except:
                    pass
                if (i // batch_size + 1) % 20 == 0:
                    logger.info(f"  Wallets: {i + len(batch)}/{min(len(wallet_rows), 5000)} | ok={ok_wallet}")
                    await asyncio.sleep(0.1)

            total_ingested += ok_scam + ok_wallet
            logger.info(f"  Done: {ok_scam} scams, {ok_wallet} wallets ingested")

        # ── 3. Solana Token Registry ──
        try:
            resp = await client.get(
                "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
            )
            data = resp.json()
            flagged = [
                t
                for t in data.get("tokens", [])
                if any(kw in str(t.get("tags", [])).lower() for kw in ["scam", "spam", "fake"])
            ]
            logger.info(f"3. Solana tokens: {len(flagged)} flagged as scam/spam")
            ok = 0
            for i, token in enumerate(flagged):
                payload = {
                    "collection": "known_scams",
                    "content": f"Solana scam token: {token.get('name', '')} ({token.get('symbol', '')}) at {token.get('address', '')}. Tags: {token.get('tags', [])}.",
                    "metadata": {
                        "address": token.get("address", ""),
                        "name": token.get("name", ""),
                        "symbol": token.get("symbol", ""),
                        "chain": "solana",
                        "source": "solana_token_registry",
                        "tags": token.get("tags", []),
                        "severity": "high",
                    },
                }
                try:
                    resp = await client.post(RAG_API, json=payload)
                    if resp.status_code == 200:
                        ok += 1
                except:
                    pass
                await asyncio.sleep(0.15)
            total_ingested += ok
            logger.info(f"  Done: {ok} ingested")
        except Exception as e:
            logger.warning(f"  Solana token fetch failed: {e}")

        # ── 4. Rekt Hacks ──
        rekt_path = os.path.join(DATA_DIR, "rekt_hacks.json")
        if os.path.exists(rekt_path):
            with open(rekt_path) as f:
                hacks = json.load(f)
            logger.info(f"4. Rekt hacks: {len(hacks)} incidents")
            ok = 0
            for i, hack in enumerate(hacks):
                payload = {
                    "collection": "forensic_reports",
                    "content": f"DeFi hack: {hack.get('name', '')} — {hack.get('description', '')}. Attackers: {hack.get('attackers', [])}. Exploited: {hack.get('exploited', [])}. Amount: {hack.get('amount_lost', '')}.",
                    "metadata": {
                        "source": "rekt_database",
                        "name": hack.get("name", ""),
                        "attackers": hack.get("attackers", []),
                        "exploited": hack.get("exploited", []),
                        "severity": "critical",
                        "chain": "multi",
                    },
                }
                try:
                    resp = await client.post(RAG_API, json=payload)
                    if resp.status_code == 200:
                        ok += 1
                except:
                    pass
                await asyncio.sleep(0.1)
            total_ingested += ok
            logger.info(f"  Done: {ok} ingested")
        else:
            logger.warning("4. Rekt hacks: file not found, skipping")

    logger.info(f"\n=== TOTAL INGESTED: {total_ingested} ===")

    # ── 5. Build FAISS indexes ──
    logger.info("\n5. Building FAISS indexes...")
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post("http://localhost:8000/api/v1/rag/build-index", json={})
            logger.info(f"  Build index response: {resp.status_code} — {resp.text[:300]}")

            # Verify
            resp2 = await client.get("http://localhost:8000/api/v1/rag/stats")
            stats = resp2.json()
            logger.info(
                f"  RAG stats: {stats.get('total_docs', 0)} total docs across {len(stats.get('collections', {}))} collections"
            )
            for name, count in stats.get("collections", {}).items():
                logger.info(f"    {name}: {count} docs")
        except Exception as e:
            logger.error(f"  FAISS build error: {e}")


asyncio.run(run())
