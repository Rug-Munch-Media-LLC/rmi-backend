"""x402 exact — paid endpoint catalog (intel + security only).

Single source of truth for what is sold, at what price, and which DataBus
provider backs it. Prices are atoms (1 atom = $0.000001 USDC) so the wire
value is an integer string, never a float.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

USDC_BASE: Final = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
NETWORK_BASE: Final = "eip155:8453"


@dataclass(frozen=True)
class PaidEndpoint:
    """One sellable resource."""

    handle: str
    path: str
    price_atoms: int
    provider: str
    tier: str
    free_daily: int
    description: str
    request_schema: dict[str, str]
    response_fields: tuple[str, ...]

    @property
    def usd(self) -> float:
        return self.price_atoms / 1_000_000


_CATALOG: Final[dict[str, PaidEndpoint]] = {
    "intel.scan": PaidEndpoint(
        handle="intel.scan",
        path="/api/v1/x402/intel/scan",
        price_atoms=5_000,
        provider="risk_scan",
        tier="basic",
        free_daily=3,
        description="Token risk scan: honeypot, tax, holder concentration, verdict.",
        request_schema={"chain": "string", "address": "string"},
        response_fields=("verdict", "risk_score", "flags", "chain", "address"),
    ),
    "security.wallet": PaidEndpoint(
        handle="security.wallet",
        path="/api/v1/x402/security/wallet",
        price_atoms=20_000,
        provider="threat_check",
        tier="premium",
        free_daily=2,
        description="Wallet security intel: threat screen, entity labels, exposure.",
        request_schema={"address": "string", "chain": "string"},
        response_fields=("risk_level", "labels", "threats", "address"),
    ),
    "scanner.token": PaidEndpoint(
        handle="scanner.token",
        path="/api/v1/x402/scanner/token",
        price_atoms=10_000,
        provider="token_security",
        tier="basic",
        free_daily=3,
        description="Token security snapshot: contract safety, liquidity, ownership.",
        request_schema={"chain": "string", "address": "string"},
        response_fields=("security", "liquidity", "ownership", "chain", "address"),
    ),
}


def resolve(handle: str) -> PaidEndpoint | None:
    """Return the endpoint for a handle, or None when it is not for sale."""
    return _CATALOG.get(handle)


def all_endpoints() -> tuple[PaidEndpoint, ...]:
    """Every sellable endpoint, stable ordering."""
    return tuple(_CATALOG[k] for k in sorted(_CATALOG))
