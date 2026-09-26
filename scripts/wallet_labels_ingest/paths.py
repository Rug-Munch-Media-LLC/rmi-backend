#!/usr/bin/env python3
"""Filesystem locations used by the wallet-label ingestion pipeline.

``DATA_DIR`` resolves to the repository ``data/`` directory. The original script
computed it as ``<script dir>/../data`` from ``scripts/ingest_wallet_labels.py``;
now the modules live one level deeper so the repo root is derived from the
package location instead.
"""

from __future__ import annotations

import os

__all__ = ["DATA_DIR", "REPO_ROOT"]

# .../rmi-backend/scripts/wallet_labels_ingest/paths.py -> .../rmi-backend
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(REPO_ROOT, "data")
