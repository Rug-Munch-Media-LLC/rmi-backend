"""
Crypto Embedder - Multi-head embedding system for crypto scam/rug detection.
============================================================================
Backward-compatible re-exports from focused modules.

Split into: models, features, providers, embedder, seed.
"""

from __future__ import annotations

from app.crypto_embeddings.embedder import CryptoEmbedder
from app.crypto_embeddings.features import (
    extract_contract_features,
    extract_transaction_features,
    extract_wallet_features,
)
from app.crypto_embeddings.models import (
    COLLECTIONS,
    DIMS,
    HEAD_DEFAULTS,
    EmbeddingResult,
)
from app.crypto_embeddings.providers import (
    ContractCodeEmbedder,
    HuggingFaceEmbedder,
    InfinityBGEEmbedder,
    LocalBGEEmbedder,
    OpenRouterEmbedder,
)
from app.crypto_embeddings.seed import KNOWN_SCAM_PATTERNS

# ── Singleton ──
_embedder: CryptoEmbedder | None = None


async def get_embedder() -> CryptoEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = CryptoEmbedder()
        await _embedder.initialize()
    return _embedder


__all__ = [
    "COLLECTIONS",
    "DIMS",
    "HEAD_DEFAULTS",
    "KNOWN_SCAM_PATTERNS",
    "ContractCodeEmbedder",
    "CryptoEmbedder",
    "EmbeddingResult",
    "HuggingFaceEmbedder",
    "InfinityBGEEmbedder",
    "LocalBGEEmbedder",
    "OpenRouterEmbedder",
    "extract_contract_features",
    "extract_transaction_features",
    "extract_wallet_features",
    "get_embedder",
]
