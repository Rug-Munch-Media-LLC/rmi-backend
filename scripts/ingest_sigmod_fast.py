#!/usr/bin/env python3
"""FAST batch ingest SIGMOD P&D dataset directly into Redis.
Skips the HTTP API bottleneck - writes Redis keys directly.
"""

import csv
import hashlib
import json
import os
import sys

sys.path.insert(0, "/root/backend")
os.chdir("/root/backend")

import redis.asyncio as redis  # noqa: E402

from app.crypto_embeddings import get_embedder  # noqa: E402

CSV_PATH = "/root/tools/dark-collection/sigmod-pnd/Data/Telegram/Labeled/pred_pump_message.csv"
REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
BATCH_SIZE = 500


async def ingest():
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    print(f"Loading {len(rows)} P&D messages into Redis...")

    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, db=REDIS_DB)
    embedder = await get_embedder()

    count = 0
    pipe = r.pipeline()
    pipe_ops = 0

    for _i, row in enumerate(rows):
        msg = row.get("message", "")[:1000]
        channel = row.get("channel_id", "unknown")
        date = row.get("date", "")

        content = f"[SIGMOD P&D] Channel {channel} - {date}: {msg}"
        doc_id = hashlib.sha256(f"sigmod_{channel}_{date}".encode()).hexdigest()[:16]

        # Generate embedding
        vec = await embedder.embed_query(content)

        key = f"rag:known_scams:{doc_id}"
        doc = json.dumps(
            {
                "content": content,
                "vector": vec,
                "metadata": {
                    "source": "sigmod_pnd_2023",
                    "channel_id": channel,
                    "date": date,
                    "scam_type": "pump_and_dump",
                },
            }
        )

        pipe.set(key, doc)
        pipe.sadd("rag:idx:known_scams", doc_id)
        pipe_ops += 2

        if pipe_ops >= BATCH_SIZE:
            await pipe.execute()
            count += BATCH_SIZE // 2
            print(f"  Progress: {count}/{len(rows)}")
            pipe = r.pipeline()
            pipe_ops = 0

    if pipe_ops > 0:
        await pipe.execute()
        count += pipe_ops // 2

    print(f"Done: {count} docs ingested into known_scams (Redis direct)")
    await r.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(ingest())
