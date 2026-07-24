"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

Bitquery provider chains: holder distribution, transaction traces,
cross-chain transfer tracking. Wrapped in try/except for optional
BitqueryProvider dependency.

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.databus.provider_core import Provider, ProviderChain, ProviderTier
from app.databus.provider_implementations import (
    _dexscreener_holders,
    _etherscan_tx_trace,
)

logger = get_logger("databus.providers.chains.bitquery")

def build_bitquery_chains(chains: dict[str, ProviderChain]) -> None:
    """Build Bitquery-backed chains (holder_data, tx_trace, cross_chain).

    Bitquery is optional; silently skipped if the module is unavailable.
    """
    # ── Bitquery chains (activate free plan at bitquery.io/pricing) ──
    try:
        from app.databus.bitquery_provider import BitqueryProvider

        _bq = BitqueryProvider()

        async def _bq_holder_data(**kw) -> Any:
            return await _bq.get_holder_distribution(
                kw.get("network", "ethereum"), kw.get("token", "")
            )

        async def _bq_tx_trace(**kw) -> Any:
            return await _bq.get_transaction_trace(
                kw.get("network", "ethereum"), kw.get("tx_hash", "")
            )

        async def _bq_cross_chain(**kw) -> Any:
            return await _bq.get_cross_chain_transfers(kw.get("address", ""))

        chains["holder_data"] = ProviderChain(
            "holder_data",
            description="Token holder distribution (DexScreener free \u2192 Bitquery freemium)",
            providers=[
                Provider(
                    "dexscreener_holders",
                    ProviderTier.FREE_API,
                    _dexscreener_holders,
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
                Provider(
                    "bitquery_holders",
                    ProviderTier.FREEMIUM,
                    _bq_holder_data,
                    weight=5.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="BITQUERY_API_KEY",
                    monthly_quota=10000,
                ),
            ],
        )
        chains["tx_trace"] = ProviderChain(
            "tx_trace",
            description="Transaction traces (Etherscan free \u2192 Bitquery freemium)",
            providers=[
                Provider(
                    "etherscan_trace",
                    ProviderTier.FREE_API,
                    _etherscan_tx_trace,
                    weight=10.0,
                    rate_limit_rps=3.0,
                ),
                Provider(
                    "bitquery_trace",
                    ProviderTier.FREEMIUM,
                    _bq_tx_trace,
                    weight=5.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="BITQUERY_API_KEY",
                    monthly_quota=10000,
                ),
            ],
        )
        chains["cross_chain"] = ProviderChain(
            "cross_chain",
            description="Cross-chain transfer tracking and bridge monitoring",
            providers=[
                Provider(
                    "bitquery_cross_chain",
                    ProviderTier.FREEMIUM,
                    _bq_cross_chain,
                    weight=5.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="BITQUERY_API_KEY",
                    monthly_quota=10000,
                )
            ],
        )
        logger.info("Bitquery chains registered: holder_data, tx_trace, cross_chain")
    except (ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e :
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Bitquery not available: {e}")
