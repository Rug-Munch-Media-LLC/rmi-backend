#!/usr/bin/env python3
"""Logging + optional-yaml setup for the wallet-label ingestion pipeline.

Split out of the original monolithic ``scripts/ingest_wallet_labels.py`` so the
logger name and the pyyaml import guard live in exactly one place.
"""

from __future__ import annotations

import logging

try:
    import yaml
except ImportError:  # pragma: no cover — pyyaml ships in the rmi-backend venv
    yaml = None

__all__ = ["logger", "yaml"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ingest_wallet_labels")
