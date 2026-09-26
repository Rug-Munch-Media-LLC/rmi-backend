#!/usr/bin/env python3
"""Wallet-label ingestion pipeline (package form of ``ingest_wallet_labels.py``).

Public surface preserved from the original single-file script:

- connection helpers: ``get_ch_client`` / ``get_redis_client``
- query shims: ``ch_execute`` / ``ch_select`` / ``make_label_id``
- writers: ``insert_wallet_labels`` / ``insert_scam_addresses``
- one ``ingest_*`` coroutine per source, plus ``show_report`` and ``main``
- ``SOURCE_REGISTRY`` — name → coroutine map used by the CLI
"""

from __future__ import annotations

from .ch_client import ch_execute, ch_select
from .cli import SOURCE_REGISTRY, main
from .clients import get_ch_client, get_redis_client
from .logging_setup import logger
from .report import show_report
from .src_bluepages_pair import ingest_bluepages, ingest_walletlabels_xyz
from .src_builtin import ingest_builtin_contracts
from .src_chainabuse import ingest_chainabuse
from .src_cryptoscamdb import ingest_cryptoscamdb
from .src_etherscan import ingest_etherscan_labels
from .src_github_community import ingest_github_community
from .src_metamask import ingest_metamask_phishing
from .src_open_labels import ingest_open_labels
from .src_rolodeth import ingest_rolodeth
from .src_scanner_findings import ingest_scanner_findings
from .store_labels import insert_wallet_labels, make_label_id
from .store_scams import insert_scam_addresses

__all__ = [
    "SOURCE_REGISTRY",
    "ch_execute",
    "ch_select",
    "get_ch_client",
    "get_redis_client",
    "ingest_bluepages",
    "ingest_builtin_contracts",
    "ingest_chainabuse",
    "ingest_cryptoscamdb",
    "ingest_etherscan_labels",
    "ingest_github_community",
    "ingest_metamask_phishing",
    "ingest_open_labels",
    "ingest_rolodeth",
    "ingest_scanner_findings",
    "ingest_walletlabels_xyz",
    "insert_scam_addresses",
    "insert_wallet_labels",
    "logger",
    "main",
    "make_label_id",
    "show_report",
]
