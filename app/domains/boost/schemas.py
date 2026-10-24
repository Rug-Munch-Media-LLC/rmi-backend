"""Pydantic v2 contracts for the paid boost surface."""

from __future__ import annotations

import re
import uuid  # noqa: TC003 — pydantic resolves annotations at class creation
from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — pydantic resolves annotations at class creation
from decimal import Decimal  # noqa: TC003 — pydantic resolves annotations at class creation

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.boost.pricing import PLANS, InvalidPlanError, get_plan

# Chains we actually index and can place a boost on.
ALLOWED_CHAINS: frozenset[str] = frozenset(
    {
        "ethereum",
        "solana",
        "base",
        "bsc",
        "arbitrum",
        "polygon",
        "optimism",
        "avalanche",
    }
)

# Same-name aliases people type; normalised to the canonical chain id.
CHAIN_ALIASES: dict[str, str] = {
    "eth": "ethereum",
    "sol": "solana",
    "bnb": "bsc",
    "binance": "bsc",
    "arb": "arbitrum",
    "matic": "polygon",
    "op": "optimism",
    "avax": "avalanche",
}

# Payment providers with a live entitlement verifier.
ALLOWED_PAYMENT_PROVIDERS: frozenset[str] = frozenset({"x402"})

_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def normalize_chain(value: str) -> str:
    """Lower-case and resolve a chain alias to its canonical id."""
    chain = (value or "").strip().lower()
    chain = CHAIN_ALIASES.get(chain, chain)
    if chain not in ALLOWED_CHAINS:
        raise ValueError(f"unsupported chain {value!r}; allowed: {sorted(ALLOWED_CHAINS)}")
    return chain


def validate_token_address(chain: str, token_address: str) -> str:
    """Chain-appropriate address shape check. Returns the trimmed address."""
    address = (token_address or "").strip()
    if chain == "solana":
        if not _SOLANA_ADDRESS.match(address):
            raise ValueError("invalid Solana token address (base58, 32-44 chars)")
        return address
    if not _EVM_ADDRESS.match(address):
        raise ValueError("invalid EVM token address (0x + 40 hex chars)")
    return address


class BoostCreateRequest(BaseModel):
    """Buy a placement. ``payment_ref`` must already be settled upstream."""

    chain: str = Field(min_length=2, max_length=32)
    token_address: str = Field(min_length=32, max_length=128)
    plan_id: str = Field(min_length=2, max_length=32)
    payment_ref: str = Field(min_length=8, max_length=256)
    payment_provider: str = Field(default="x402", max_length=32)
    buyer_ref: str | None = Field(default=None, max_length=128)

    @field_validator("chain")
    @classmethod
    def _chain(cls, value: str) -> str:
        return normalize_chain(value)

    @field_validator("plan_id")
    @classmethod
    def _plan(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        try:
            get_plan(normalized)
        except InvalidPlanError as exc:
            raise ValueError(str(exc)) from exc
        return normalized

    @field_validator("payment_provider")
    @classmethod
    def _provider(cls, value: str) -> str:
        provider = (value or "").strip().lower()
        if provider not in ALLOWED_PAYMENT_PROVIDERS:
            raise ValueError(f"unsupported payment provider {value!r}; allowed: {sorted(ALLOWED_PAYMENT_PROVIDERS)}")
        return provider

    @field_validator("token_address")
    @classmethod
    def _token(cls, value: str, info: object) -> str:
        chain = ""
        if hasattr(info, "data"):
            chain = str(info.data.get("chain") or "")
        return validate_token_address(chain, value)


class BoostOut(BaseModel):
    """Public representation of one purchase."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chain: str
    token_address: str
    plan_id: str
    duration_hours: int
    price_amount: Decimal
    currency: str
    payment_provider: str
    payment_ref: str | None
    status: str
    starts_at: datetime | None
    ends_at: datetime | None


class BoostListResponse(BaseModel):
    """Active paid placements for one chain."""

    chain: str
    count: int
    currency: str
    items: list[BoostOut]


class BoostPlanOut(BaseModel):
    plan_id: str
    duration_hours: int
    price_amount: Decimal
    currency: str
    label: str


class BoostPlansResponse(BaseModel):
    currency: str
    plans: list[BoostPlanOut]


class ExpireResponse(BaseModel):
    expired: int


def plan_catalog() -> BoostPlansResponse:
    """Published catalog in a stable order (shortest duration first)."""
    ordered = sorted(PLANS.values(), key=lambda p: p.duration_hours)
    return BoostPlansResponse(
        currency=ordered[0].currency if ordered else "USD",
        plans=[BoostPlanOut(**asdict(plan)) for plan in ordered],
    )
