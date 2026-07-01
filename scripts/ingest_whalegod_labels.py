#!/usr/bin/env python3
"""Import WHALEGOD wallet labels into Wallet Memory Bank."""

import os
import sys

sys.path.insert(0, "/root/backend")
os.chdir("/root/backend")

# Extract labels from WHALEGOD source
whale_path = "/root/tools/dark-collection/whalegod/bot/services/label_service.py"
namespace = {}
with open(whale_path) as f:
    exec(f.read(), namespace)

eth_labels = namespace.get("ETH_LABELS", {})
sol_labels = namespace.get("SOL_LABELS", {})

print(f"WHALEGOD labels: ETH={len(eth_labels)}, SOL={len(sol_labels)}")

# Ingest into wallet_memory
import asyncio

import httpx


async def ingest():
    count = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for addr, info in {**eth_labels, **sol_labels}.items():
            entity_type = info.get("type", "unknown")
            entity_name = info.get("entity", info.get("name", "unknown"))

            payload = {
                "collection": "wallet_profiles",
                "content": f"WHALEGOD label: {info.get('name', addr)} - type={entity_type} entity={entity_name}",
                "metadata": {
                    "address": addr,
                    "source": "whalegod",
                    "entity_type": entity_type,
                    "entity_name": entity_name,
                    "label_name": info.get("name", ""),
                },
            }

            try:
                r = await client.post(
                    "http://localhost:8000/api/v1/rag/ingest",
                    json=payload,
                    headers={"X-RMI-Key": "rmi-internal-2026"},
                )
                if r.status_code == 200:
                    count += 1
            except Exception:
                pass

        print(f"Ingested {count}/{len(eth_labels) + len(sol_labels)} WHALEGOD labels")


asyncio.run(ingest())
