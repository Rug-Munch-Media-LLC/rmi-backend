"""
x402 Facilitator Configuration
===============================
Environment variable mapping and configuration for all facilitators.
Each facilitator loads its config from environment variables.

Usage:
    from app.domains.billing.facilitators.config import FacilitatorConfig
    cfg = FacilitatorConfig()

    cdp_url = cfg.coinbase_cdp_url
    asterpay_key = cfg.asterpay_api_key
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FacilitatorConfig:
    """Centralized configuration for all x402 facilitators."""

    # ── Coinbase CDP ──────────────────────────────────────────
    coinbase_cdp_api_key: str = field(default_factory=lambda: os.getenv("COINBASE_CDP_API_KEY", ""))
    coinbase_cdp_api_secret: str = field(default_factory=lambda: os.getenv("COINBASE_CDP_API_SECRET", ""))
    coinbase_cdp_network: str = field(default_factory=lambda: os.getenv("COINBASE_CDP_NETWORK", "base-mainnet"))
    coinbase_cdp_verify_url: str = field(
        default_factory=lambda: os.getenv(
            "COINBASE_CDP_VERIFY_URL",
            "https://api.developer.coinbase.com/cdp/x402/verify",
        )
    )

    # ── PayAI ─────────────────────────────────────────────────
    payai_verify_url: str = field(
        default_factory=lambda: os.getenv("PAYAI_VERIFY_URL", "https://facilitator.payai.network/verify")
    )
    payai_settle_url: str = field(
        default_factory=lambda: os.getenv("PAYAI_SETTLE_URL", "https://facilitator.payai.network/settle")
    )

    # ── Cloudflare x402 ───────────────────────────────────────
    cloudflare_x402_url: str = field(
        default_factory=lambda: os.getenv("CLOUDFLARE_X402_URL", "https://x402.org/facilitator")
    )

    # ── BNB Chain Pieverse [OFFLINE - NXDOMAIN - DISABLED] ──
    # api.pieverse.xyz DNS is NXDOMAIN - this facilitator is dead
    # Disabled: enables_at_zero and skips default URL on timeout
    pieverse_api_key: str = field(default_factory=lambda: os.getenv("PIEVERSE_API_KEY", ""))
    pieverse_verify_url: str = field(default_factory=lambda: "")
    pieverse_settle_url: str = field(default_factory=lambda: "")

    # ── AsterPay (EUR/SEPA) ───────────────────────────────────
    asterpay_api_key: str = field(default_factory=lambda: os.getenv("ASTERPAY_API_KEY", ""))
    asterpay_api_secret: str = field(default_factory=lambda: os.getenv("ASTERPAY_API_SECRET", ""))
    asterpay_verify_url: str = field(
        default_factory=lambda: os.getenv("ASTERPAY_VERIFY_URL", "https://api.asterpay.io/x402/verify")
    )
    asterpay_settle_url: str = field(
        default_factory=lambda: os.getenv("ASTERPAY_SETTLE_URL", "https://api.asterpay.io/x402/settle")
    )
    asterpay_eur_offramp_url: str = field(
        default_factory=lambda: os.getenv("ASTERPAY_EUR_OFFRAMP_URL", "https://api.asterpay.io/offramp/sepa")
    )
    asterpay_sepa_iban: str = field(default_factory=lambda: os.getenv("ASTERPAY_SEPA_IBAN", ""))

    # ── MERX x402 for TRON [OFFLINE - NXDOMAIN - DISABLED] ──
    # api.merx.finance DNS is NXDOMAIN - this facilitator is dead
    merx_tron_api_key: str = field(default_factory=lambda: os.getenv("MERX_TRON_API_KEY", ""))
    merx_tron_verify_url: str = field(default_factory=lambda: "")
    merx_tron_settle_url: str = field(default_factory=lambda: "")

    # ── Primev FastRPC ───────────────────────────────────────
    primev_rpc_url: str = field(default_factory=lambda: os.getenv("PRIMEV_RPC_URL", "https://mev-commit.primev.xyz"))
    primev_agent_id: str = field(default_factory=lambda: os.getenv("PRIMEV_AGENT_ID", "23175"))
    primev_api_key: str = field(default_factory=lambda: os.getenv("PRIMEV_API_KEY", ""))

    # ── Satoshi Facilitator (Bitcoin) [OFFLINE - NXDOMAIN - DISABLED] ──
    # api.satoshi.dev DNS is NXDOMAIN - this facilitator is dead
    satoshi_api_key: str = field(default_factory=lambda: os.getenv("SATOSHI_API_KEY", ""))
    satoshi_verify_url: str = field(default_factory=lambda: "")
    satoshi_settle_url: str = field(default_factory=lambda: "")
    satoshi_btc_settle_url: str = field(default_factory=lambda: "")

    # ── Self-Hosted x402-rs ───────────────────────────────────
    x402_rs_host: str = field(default_factory=lambda: os.getenv("X402_RS_HOST", "localhost"))
    x402_rs_port: int = field(default_factory=lambda: int(os.getenv("X402_RS_PORT", "8090")))
    x402_rs_verify_url: str = field(
        default_factory=lambda: os.getenv("X402_RS_VERIFY_URL", "http://localhost:8090/verify")
    )
    x402_rs_settle_url: str = field(
        default_factory=lambda: os.getenv("X402_RS_SETTLE_URL", "http://localhost:8090/settle")
    )

    # ── EIP-7702 Universal EVM ────────────────────────────────
    eip7702_rpc_urls: dict[str, str] = field(default_factory=dict)
    eip7702_default_rpcs: dict[str, str] = field(
        default_factory=lambda: {
            "bsc": os.getenv("EIP7702_BSC_RPC", "https://bsc-dataseed.binance.org"),
            "polygon": os.getenv("EIP7702_POLYGON_RPC", "https://polygon-rpc.com"),
            "avalanche": os.getenv("EIP7702_AVAX_RPC", "https://api.avax.network/ext/bc/C/rpc"),
            "fantom": os.getenv("EIP7702_FANTOM_RPC", "https://rpc.ftm.tools"),
            "gnosis": os.getenv("EIP7702_GNOSIS_RPC", "https://rpc.gnosischain.com"),
            "arbitrum": os.getenv("EIP7702_ARBITRUM_RPC", "https://arb1.arbitrum.io/rpc"),
            "optimism": os.getenv("EIP7702_OPTIMISM_RPC", "https://mainnet.optimism.io"),
            "base": os.getenv("EIP7702_BASE_RPC", "https://mainnet.base.org"),
        }
    )
    eip7702_delegation_address: str = field(
        default_factory=lambda: os.getenv("EIP7702_DELEGATION_ADDRESS", "0x0000000000000000000000000000000000000000")
    )

    # ── General Facilitator Settings ──────────────────────────
    facilitator_health_ttl: int = field(default_factory=lambda: int(os.getenv("FACILITATOR_HEALTH_TTL", "60")))
    facilitator_verify_timeout: int = field(default_factory=lambda: int(os.getenv("FACILITATOR_VERIFY_TIMEOUT", "15")))
    facilitator_max_retries: int = field(default_factory=lambda: int(os.getenv("FACILITATOR_MAX_RETRIES", "2")))

    # ── Pay-to addresses ──────────────────────────────────────
    evm_pay_to: str = field(
        default_factory=lambda: os.getenv("X402_EVM_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9")
    )
    sol_pay_to: str = field(
        default_factory=lambda: os.getenv("X402_SOL_PAY_TO", "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv")
    )
    tron_pay_to: str = field(default_factory=lambda: os.getenv("X402_TRON_PAY_TO", ""))
    btc_pay_to: str = field(default_factory=lambda: os.getenv("X402_BTC_PAY_TO", ""))

    def to_dict(self) -> dict[str, Any]:
        """Export config as dict (excluding secrets)."""
        import dataclasses

        d = dataclasses.asdict(self)
        # Redact secrets
        for k in list(d.keys()):
            if any(secret_word in k for secret_word in ["secret", "key", "password", "token"]):
                val = d[k]
                if val and isinstance(val, str) and len(val) > 4:
                    d[k] = val[:4] + "..." + val[-4:] if len(val) > 12 else "***"
        return d


# Singleton
_config: FacilitatorConfig | None = None


def get_config() -> FacilitatorConfig:
    global _config
    if _config is None:
        _config = FacilitatorConfig()
    return _config


def reset_config() -> None:
    global _config
    _config = None
