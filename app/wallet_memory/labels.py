"""
Unified Label Service - Merges wallet_label_loader + address_labeler into one call.
===============================================================================
THIN ADAPTER. Canonical source: `rugmunch_scanner.wallet_memory.labels`.
The implementation was superseded by the private rugmunch-scanner repo.
"""

from rugmunch_scanner.wallet_memory.labels import (
    LabelService,
    get_label_service,
)

__all__ = [
    "LabelService",
    "get_label_service",
]
