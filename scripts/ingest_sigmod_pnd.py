#!/usr/bin/env python3
"""Ingest SIGMOD 2023 Pump & Dump dataset into RAG known_scams.
36,696 labeled messages from Telegram pump channels - ground truth training data.
"""

import asyncio
import csv
import sys

import httpx

sys.path.insert(0, "/root/backend")

CSV_PATH = "/root/tools/dark-collection/sigmod-pnd/Data/Telegram/Labeled/pred_pump_message.csv"
RAG_URL = "http://localhost:8000/api/v1/rag/ingest"
BATCH = 100  # Ingest in batches to avoid overwhelming the API


async def ingest():
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    print(f"Loading {len(rows)} P&D messages...")

    count = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for i, row in enumerate(rows):
            # Build a searchable document
            msg = row.get("message", "")[:1000]
            channel = row.get("channel_id", "unknown")
            date = row.get("date", "")
            label = row.get("pred_label", "0")

            payload = {
                "collection": "known_scams",
                "content": f"[SIGMOD P&D] Channel {channel} - {date}: {msg}",
                "metadata": {
                    "source": "sigmod_pnd_2023",
                    "channel_id": channel,
                    "date": date,
                    "pred_label": float(label),
                    "scam_type": "pump_and_dump",
                    "dataset": "SIGMOD 2023",
                },
            }

            try:
                r = await client.post(RAG_URL, json=payload, headers={"X-RMI-Key": "rmi-internal-2026"})
                if r.status_code == 200:
                    count += 1
            except Exception:
                pass

            if (i + 1) % 500 == 0:
                print(f"  Progress: {i + 1}/{len(rows)} ({count} ingested)")

        print(f"Done: {count}/{len(rows)} ingested into known_scams")


if __name__ == "__main__":
    asyncio.run(ingest())
