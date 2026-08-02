"""
Chain Indexer Own-Data Providers — LOCAL-tier DataBus providers
================================================================
Exposes the Phase 4 own-data scanner engines through the DataBus as
LOCAL-tier providers. Used when PREFER_OWN_DATA=true (the default) so
scanner/data pipelines can call:

  DataBus fetch("deployer_reputation", ...) → own_deployer_reputation
  DataBus fetch("whale_detection", ...)     → own_whale_detect
  DataBus fetch("volume_authenticity", ...) → own_volume_authenticity
  DataBus fetch("holder_age", ...)          → own_holder_age

Each provider delegates to the corresponding scanner engine which queries
ClickHouse (wallet_memory.deployer_events, dex_events, evm_transfers).
Every function returns dict | None following the DataBus provider contract.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.modules._core import PREFER_OWN_DATA

logger = get_logger("databus.providers.chain_indexer_own")


async def _chain_indexer_deployer_reputation(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — deployer reputation (0-100) from ClickHouse deployer_events.

    Parameters (from DataBus fetch kwargs):
      deployer: str — deployer wallet address
      address: str — alias for deployer
      chain: str — optional chain filter
      check_tokens: int — max recent tokens to deep-scan (optional)

    Returns dict with reputation score/metrics or None.
    """
    if not PREFER_OWN_DATA:
        return None

    deployer = kwargs.get("deployer", kwargs.get("address", "")).lower()
    chain = kwargs.get("chain", "")
    check_tokens = int(kwargs.get("check_tokens", 10))

    if not deployer:
        return None

    try:
        from app.domains.scanner.engines.own_deployer_reputation import (
            get_own_deployer_reputation,
        )

        result = await get_own_deployer_reputation(
            deployer_address=deployer,
            chain=chain,
            check_tokens=check_tokens,
        )
        if not result or result.get("result") != "scanned" or result.get("confidence", 0) <= 0:
            return None

        return {
            "data": result,
            "source": "chain_indexer (ClickHouse deployer_events reputation)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_deployer_reputation failed: %s", exc)
        return None


async def _chain_indexer_whale_detect(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — whale wallet detection from ClickHouse dex_events.

    Parameters (from DataBus fetch kwargs):
      token_address: str — token contract address
      chain: str — chain name
      min_usd_threshold: float — whale USD threshold (default 10000)
      lookback_hours: int — analysis window (default 168)

    Returns dict with whale wallets/concentration or None.
    """
    if not PREFER_OWN_DATA:
        return None

    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = kwargs.get("chain", "")
    min_usd = float(kwargs.get("min_usd_threshold", 10000.0) or 10000.0)
    lookback_hours = int(kwargs.get("lookback_hours", 168))

    if not token_address or not chain:
        return None

    try:
        from app.domains.scanner.engines.own_whale_detect import get_own_whale_detection

        result = await get_own_whale_detection(
            token_address=token_address,
            chain=chain,
            min_usd_threshold=min_usd,
            lookback_hours=lookback_hours,
        )
        if not result or result.get("result") != "scanned" or not result.get("whale_wallets"):
            return None

        return {
            "data": result,
            "source": "chain_indexer (ClickHouse dex_events whale detect)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_whale_detect failed: %s", exc)
        return None


async def _chain_indexer_volume_authenticity(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — wash-trading / volume authenticity from ClickHouse.

    Parameters (from DataBus fetch kwargs):
      token_address: str — token contract address
      chain: str — chain name
      lookback_hours: int — analysis window (default 24)

    Returns dict with authentic_score + wash signals or None.
    """
    if not PREFER_OWN_DATA:
        return None

    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = kwargs.get("chain", "")
    lookback_hours = int(kwargs.get("lookback_hours", 24))

    if not token_address or not chain:
        return None

    try:
        from app.domains.scanner.engines.own_volume_authenticity import (
            get_own_volume_authenticity,
        )

        result = await get_own_volume_authenticity(
            token_address=token_address,
            chain=chain,
            lookback_hours=lookback_hours,
        )
        if not result or result.get("result") != "scanned" or result.get("confidence", 0) <= 0:
            return None

        return {
            "data": result,
            "source": "chain_indexer (ClickHouse volume authenticity)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_volume_authenticity failed: %s", exc)
        return None


async def _chain_indexer_holder_age(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — holder age distribution from ClickHouse evm_transfers.

    Parameters (from DataBus fetch kwargs):
      token_address: str — token contract address
      chain: str — chain name

    Returns dict with holder age distribution + flags or None.
    """
    if not PREFER_OWN_DATA:
        return None

    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = kwargs.get("chain", "")

    if not token_address or not chain:
        return None

    try:
        from app.domains.scanner.engines.own_holder_age import get_own_holder_age

        result = await get_own_holder_age(
            token_address=token_address,
            chain=chain,
        )
        if not result or result.get("result") != "scanned" or result.get("holder_count", 0) <= 0:
            return None

        return {
            "data": result,
            "source": "chain_indexer (ClickHouse holder age)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_holder_age failed: %s", exc)
        return None
