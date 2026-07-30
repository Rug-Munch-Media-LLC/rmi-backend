"""
Indexer domain — LiteChainIndexer integration.

Provides the LiteIndexer class, CLI interface, and deployer tracker.
Re-exported from the standalone lite-chain-indexer package.

Usage:
    python3 -m app.domains.indexer.lite_indexer --chain ethereum
    python3 -m app.domains.indexer.lite_indexer --dex --daemon
    python3 -m app.domains.indexer.deployer_tracker  # Standalone deployer sweep
"""

from lite_indexer import (
    BATCH_SIZE,
    CHAIN_POLL_INTERVALS,
    DISK_CAP_BYTES,
    GET_LOGS_ENDPOINTS,
    LiteIndexer,
    async_main,
    main,
    main_daemon,
    parse_args,
)

from app.domains.indexer.deployer_tracker import (
    process_block_deployers,
    run_deployer_tracker,
    write_deployer_events,
)

__all__ = [
    "LiteIndexer",
    "main",
    "async_main",
    "main_daemon",
    "parse_args",
    "GET_LOGS_ENDPOINTS",
    "CHAIN_POLL_INTERVALS",
    "BATCH_SIZE",
    "DISK_CAP_BYTES",
    # Deployer tracker (Phase 2)
    "process_block_deployers",
    "write_deployer_events",
    "run_deployer_tracker",
]
