#!/usr/bin/env python3
"""RugMaps Qdrant wallet seeder — populate ``rgm_wallets`` from ClickHouse.

Phase 0→1 companion to ``app.domains.rugmaps.seed``. Reads labeled wallets
from ClickHouse (``wallet_labels``), embeds the label text via Infinity
(``BAAI/bge-m3``), and upserts 1024-dim ``dense`` vectors into the
``rgm_wallets`` collection on the shared Qdrant instance.

Usage:
    # Seed the top 10,000 labeled wallets (idempotent — replaces by point ID):
    python scripts/ops/rugmaps_seed_wallets.py --limit 10000

    # Dry run — fetch + embed but do NOT upsert:
    python scripts/ops/rugmaps_seed_wallets.py --limit 1000 --dry-run

    # Custom endpoints:
    python scripts/ops/rugmaps_seed_wallets.py \
        --qdrant-url http://localhost:6333 \
        --embed-url http://localhost:8001

Env: CLICKHOUSE_HOST/PORT/USER/PASSWORD/DB, QDRANT_URL, INFINITY_URL.

Exit code 0 when no embed/upsert failures were recorded.
"""

from __future__ import annotations

from app.domains.rugmaps.seed import main

if __name__ == "__main__":
    raise SystemExit(main())
