"""RMI Token Scanner - Multi-Chain Security Analysis.

Per v4.0 §T26. Scan tokens for honeypots, mintability, proxy contracts,
and other rug-pull indicators across 21+ modules.

FREE tier:   SENTINEL Tier 1 (12 core modules) + DexScreener + GoPlus
PRO tier:    SENTINEL Tier 1+2 (17 modules) + wallet intelligence
ELITE tier:  SENTINEL Tier 1+2+3+4 (all 21+ modules) + deep analysis

Architecture:
  - app/domain/scanner/models.py     ScanResult, CHAIN_IDS
  - app/domain/scanner/market_data.py DexScreener, Solana RPC
  - app/domain/scanner/modules.py    12 core scanner modules
  - app/domain/scanner/service.py    scan_token() + quick_scan_text()
  - app/token_scanner.py             Backward compatibility shim
"""

from typing import Any

from app.domains.scanner.models import ScanResult


def quick_scan_text(*args, **kwargs) -> Any:
    """Lazy import to avoid circular dependency with modules package."""
    from app.domains.scanner.service import quick_scan_text as _f

    return _f(*args, **kwargs)


def scan_token(*args, **kwargs) -> Any:
    """Lazy import to avoid circular dependency with modules package."""
    from app.domains.scanner.service import scan_token as _f

    return _f(*args, **kwargs)


__all__ = ["ScanResult", "quick_scan_text", "scan_token"]
