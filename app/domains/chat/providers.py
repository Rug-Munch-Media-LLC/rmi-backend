"""Ordered free-model chain for the brand chat.

Order is deliberate: cleanest answer first, then proven-fast OpenRouter free lanes, then a direct
NVIDIA route for the same model (different infrastructure, different quota),
then Mistral's free-tier experiment plan, then the local llama.cpp extractor
lane (frequently saturated, so short timeout + no retry). Retrieval-only is the
implicit final step handled by ``model_chain.complete``.

Measured 2026-10-23 (live): nemotron-3-ultra answers in plain text (~3.4 s);
``cohere/north-mini-code:free`` is the fastest lane (~0.6 s) but emits its
chain-of-thought into ``content`` at small ``max_tokens`` - kept second so it
takes over as a speed fallback when the reasoning lanes are rate-limited.

Every entry is free. A paid entry is only honoured when ``CHAT_ALLOW_PAID=1``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

RETRIEVAL_PROVIDER = "retrieval_only"
DEFAULT_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class ProviderSpec:
    """One routable chat endpoint."""

    provider: str          # logical provider label returned to callers
    model: str             # model id sent upstream
    url: str               # OpenAI-compatible /chat/completions endpoint
    key_env: str           # env var holding the bearer token ("" = no auth)
    free: bool = True
    timeout_s: float = DEFAULT_TIMEOUT_S
    retry: bool = True

    @property
    def key(self) -> str:
        """Breaker key — one per provider+model pair."""
        return f"{self.provider}:{self.model}"

    @property
    def allowed(self) -> bool:
        """Cost guard: paid lanes need an explicit opt-in."""
        if self.free:
            return True
        return os.getenv("CHAT_ALLOW_PAID", "0") == "1"


CHAIN: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        provider="openrouter",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        url="https://openrouter.ai/api/v1/chat/completions",
        key_env="OPENROUTER_API_KEY",
    ),
    ProviderSpec(
        provider="openrouter",
        model="cohere/north-mini-code:free",
        url="https://openrouter.ai/api/v1/chat/completions",
        key_env="OPENROUTER_API_KEY",
    ),
    ProviderSpec(
        provider="nvidia",
        model="nvidia/nemotron-3-ultra-550b-a55b",
        url="https://integrate.api.nvidia.com/v1/chat/completions",
        key_env="NVIDIA_API_KEY",
    ),
    ProviderSpec(
        provider="mistral",
        model="ministral-8b-latest",
        url="https://api.mistral.ai/v1/chat/completions",
        key_env="MISTRAL_API_KEY",
    ),
    ProviderSpec(
        provider="mistral",
        model="open-mistral-nemo",
        url="https://api.mistral.ai/v1/chat/completions",
        key_env="MISTRAL_API_KEY",
    ),
    ProviderSpec(
        provider="local",
        model="local",
        url="http://127.0.0.1:8015/v1/chat/completions",
        key_env="",
        timeout_s=6.0,
        retry=False,
    ),
)


def resolve_chain() -> tuple[ProviderSpec, ...]:
    """The chain minus paid lanes the operator has not opted into."""
    return tuple(spec for spec in CHAIN if spec.allowed)


def refusal_reasons() -> tuple[str, ...]:
    """Paid lanes skipped by the cost guard (surfaced in ``errors[]``)."""
    return tuple(
        f"{spec.key}: paid model refused (set CHAT_ALLOW_PAID=1 to allow)"
        for spec in CHAIN
        if not spec.allowed
    )
