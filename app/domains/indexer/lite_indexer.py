#!/usr/bin/env python3
"""
LiteIndexer — Minimal blockchain indexer using free RPCs.

This module is a thin re-export from the standalone lite-chain-indexer package.
The full implementation lives at:
    /srv/work/repos/lite-chain-indexer/lite_indexer/

Usage:
    python3 -m app.domains.indexer.lite_indexer --chain ethereum
    python3 -m app.domains.indexer.lite_indexer --chain ethereum --from-block 25645000
"""

from __future__ import annotations

import sys

from lite_indexer import (
    LiteIndexer,
    async_main as _async_main,
    main as _main,
    main_daemon as _main_daemon,
    parse_args as _parse_args,
)

# Re-export all public API for backward compatibility
__all__ = [
    "LiteIndexer",
    "async_main",
    "main",
    "main_daemon",
    "parse_args",
]

# Bind to module-level for backward compatibility
async_main = _async_main
main = _main
main_daemon = _main_daemon
parse_args = _parse_args

if __name__ == "__main__":
    sys.exit(main())
