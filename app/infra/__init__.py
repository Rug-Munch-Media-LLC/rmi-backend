"""External integrations.

    - ollama: local LLM client
    - langfuse: tracing (legacy wrapper, see core.tracing)
    - vector_store: Qdrant wrapper
    - chains/: 16 chain clients (14 EVM + Solana + Tron)
    - apis/: third-party APIs (coingecko, etherscan, birdeye, ...)
    - providers/: AI providers (ollama, openrouter, huggingface, ...)

Domain code goes through these - no direct third-party calls from domain.
"""

from __future__ import annotations
