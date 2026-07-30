"""
Unified Token Scanner package — split from unified_token_scanner.py.

Single source of truth for ALL token scanning at RMI.
"""

from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.scanner import UnifiedTokenScanner

_scanner: UnifiedTokenScanner | None = None


def get_token_scanner() -> UnifiedTokenScanner:
    global _scanner
    if _scanner is None:
        _scanner = UnifiedTokenScanner()
    return _scanner


__all__ = [
    "ScanResult",
    "UnifiedTokenScanner",
    "get_token_scanner",
]
