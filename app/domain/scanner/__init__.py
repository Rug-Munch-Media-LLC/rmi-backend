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

from app.domain.scanner.service import ScanResult, quick_scan_text, scan_token

__all__ = ["ScanResult", "quick_scan_text", "scan_token"]
