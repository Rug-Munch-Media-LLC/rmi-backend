"""Backward-compatible shim — imports from app.domains.scanner.modules package.

The real implementation lives in the ``app.domains.scanner.modules`` package
(``_core.py``, ``security.py``, ``deployer.py``, ``holders.py``, ``market.py``,
``social.py``, ``chain_pattern_detector.py``). This shim keeps the legacy
``app.domains.scanner.modules`` import path working for callers such as
``app/domains/scanner/service/scan.py``. The package ``__all__`` is the
authoritative export list.
"""

from __future__ import annotations

from app.domains.scanner.modules import *
