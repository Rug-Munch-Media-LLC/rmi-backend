#!/usr/bin/env python3
"""Entry point for the wallet-label ingestion pipeline.

The implementation now lives in the ``wallet_labels_ingest`` package; this file
remains so every existing invocation keeps working unchanged:

    python3 scripts/ingest_wallet_labels.py --all
    python3 scripts/ingest_wallet_labels.py --source builtin_contracts --chain ethereum
    python3 scripts/ingest_wallet_labels.py --scam-sources --quiet
    python3 scripts/ingest_wallet_labels.py --report
"""

from __future__ import annotations

import asyncio
import os
import sys

# Add parent dir for imports (identical to the original script)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wallet_labels_ingest import main

__all__ = ["main"]


if __name__ == "__main__":
    asyncio.run(main())
