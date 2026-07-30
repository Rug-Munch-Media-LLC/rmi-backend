"""x402 shared models — Pydantic request/response types."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domains.billing.x402.shared_data import HUMAN_PAYMENT_TOKENS


class TokenRequest(BaseModel):
    address: str
    chain: str = "solana"


class WalletRequest(BaseModel):
    address: str
    chain: str = "solana"


class SmartMoneyRequest(BaseModel):
    chain: str = "solana"
    threshold: float = 10000.0
    limit: int = 20


class URLRequest(BaseModel):
    url: str


class SentimentRequest(BaseModel):
    token: str
    chain: str = "solana"


class ClusterRequest(BaseModel):
    address: str
    chain: str = "solana"
    depth: int = 3


class InsiderRequest(BaseModel):
    creator_address: str
    chain: str = "solana"


# TwitterRequest and MarketRequest are defined in legacy code but never
# referenced by any @router endpoint. Surfaced here for traceability.
class TwitterRequest(BaseModel):  # pragma: no cover — legacy, unused
    query: str
    user: str | None = None
    handle: str | None = None
    tweet_id: str | None = None


class MultiTokenRequest(BaseModel):
    addresses: list[str]
    chain: str = "solana"


class WalletListRequest(BaseModel):
    addresses: list[str]
    chain: str = "solana"


class MarketRequest(BaseModel):  # pragma: no cover — legacy, unused
    chain: str = "all"
    hours: int = 24


class GenericRequest(BaseModel):
    address: str | None = None
    chain: str = "solana"
    token: str | None = None
    query: str | None = None
    hours: int = 24
    threshold: float = 10000.0
    limit: int = 20


class BundleRequest(BaseModel):
    address: str = ""
    token: str = ""
    wallet: str = ""
    chain: str = "base"
    url: str = ""


class MCPProxyRequest(BaseModel):
    service: str
    tool: str
    arguments: dict[str, Any] = {}


class HumanPaymentRequest(BaseModel):
    tool: str = Field(..., description="Tool ID to execute")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool parameters")
    payment_token: str = Field(..., description=f"Payment token key: {', '.join(HUMAN_PAYMENT_TOKENS.keys())}")
    tx_hash: str = Field(..., description="Transaction hash on-chain")
    wallet: str = Field(..., description="Payer wallet address")
    chain: str | None = Field(default=None, description="Blockchain (auto-detected from payment_token if not set)")


class HumanPaymentMethodsResponse(BaseModel):
    """Response listing all payment methods available to humans."""

    tokens: list[dict[str, Any]]
    pay_to_addresses: dict[str, str]
    chain_count: int
    token_count: int


class AuditRequest(BaseModel):
    address: str
    chain: str = "solana"


class SmartMoneyAlphaRequest(BaseModel):
    wallet: str
    chain: str = "solana"


class MemeVibeRequest(BaseModel):
    token: str
    chain: str = "solana"


class SentinelScanRequest(BaseModel):
    """Full 9-module SENTINEL deep scan."""

    address: str
    chain: str = "solana"
    dev_address: str | None = None


class SentinelModuleRequest(BaseModel):
    """Single SENTINEL module request."""

    address: str
    chain: str = "solana"
    dev_address: str | None = None


# Re-exported for tools that mirror legacy symbols
