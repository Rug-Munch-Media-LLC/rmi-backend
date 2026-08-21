"""Feature composition root.

Provider wrappers that bridge core DI to feature-domain singletons live
HERE — not in ``app.core`` — so core stays foundational (see
``.importlinter`` contract ``core-foundation``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domains.scanner.unified import UnifiedTokenScanner
    from app.domains.wallet.unified_wallet_scanner import UnifiedWalletScanner


def get_token_scanner() -> UnifiedTokenScanner:
    """Token scanner singleton (50+ security modules)."""
    from app.domains.scanner.unified import get_token_scanner as _get

    return _get()


def get_wallet_scanner() -> UnifiedWalletScanner:
    """Wallet scanner singleton (entity clustering, RAG, risk scoring)."""
    from app.domains.wallet.unified_wallet_scanner import get_wallet_scanner as _get

    return _get()
