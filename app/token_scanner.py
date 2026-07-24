"""Backward compatibility shim for app/token_scanner.py.

The token scanner has been refactored to app/domain/scanner/*.py for
better organization (per architecture standard: NO file > 500 lines).

This file re-exports the main API from the new modules.
"""

from __future__ import annotations
from app.domains.scanner import ScanResult, quick_scan_text, scan_token

__all__ = ["ScanResult", "quick_scan_text", "scan_token"]
