"""Embedding configuration and data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Embedding dimensions by model
DIMS = {
    "local/bge-small-en-v1.5": 384,
    "openai/text-embedding-3-large": 3072,
    "openai/text-embedding-3-small": 1536,
    "qwen3-embedding-4b": 2560,
    "Qwen/Qwen3-Embedding-4B": 2560,  # current model (DeepInfra primary + local :8004 backup)
    # Legacy BGE entries removed 2026-09-02 — superseded by Qwen3-Embedding-4B (2560-dim).
    # Retained dimension history: legacy 1024-dim (m3/large), 384-dim (small).
    "nvidia/llama-nemotron-embed-vl-1b-v2:free": 2048,
    "rmi/qwen3-embedding:4b": 2048,
    "crypto_behavioral": 64,
    "crypto_code_hash": 128,
}

HEAD_DEFAULTS = {
    "semantic": "rmi/qwen3-embedding-4b",
    "code": "rmi/qwen3-embedding-4b",
    "behavioral": "crypto_behavioral",
    "entity": "crypto_behavioral",
}

OR_PRIMARY_EMBED = "rmi/qwen3-embedding-4b"
OR_PAID_FALLBACK = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
OR_FALLBACK_EMBED = "local/bge-small-en-v1.5"
NVIDIA_EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"

COLLECTIONS = [
    "wallet_profiles",
    "token_analysis",
    "scam_patterns",
    "forensic_reports",
    "market_intel",
    "contract_audits",
    "known_scams",
    "news_articles",
    "transaction_patterns",
    "defi_hacks",
    "rug_timeline",
    "vuln_patterns",
    "crime_reports",
]


@dataclass
class EmbeddingResult:
    vector: list[float]
    dims: int
    model: str
    head: str
    metadata: dict[str, Any] = field(default_factory=dict)
    cached: bool = False
