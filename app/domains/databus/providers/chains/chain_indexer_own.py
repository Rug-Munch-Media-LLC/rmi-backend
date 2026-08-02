"""
Chain Indexer Own-Data Providers — LOCAL-tier DataBus providers
================================================================
Exposes the Phase 4 own-data scanner engines through the DataBus as
LOCAL-tier providers. Used when PREFER_OWN_DATA=true (the default) so
scanner/data pipelines can call:

  DataBus fetch("deployer_reputation", ...)  → own_deployer_reputation
  DataBus fetch("deployer_fingerprint", ...) → deployer_fingerprint (C7 v1)
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


async def _chain_indexer_deployer_fingerprint(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — deployer behavioral fingerprint (C7 v1).

    Parameters (from DataBus fetch kwargs):
      deployer: str — deployer wallet address
      address: str — alias for deployer
      chain: str — optional chain filter (default ethereum)

    Returns dict with risk bucket / risk score / evidence or None.
    """
    if not PREFER_OWN_DATA:
        return None

    deployer = kwargs.get("deployer", kwargs.get("address", "")).lower()
    chain = kwargs.get("chain", "")

    if not deployer:
        return None

    try:
        from app.domains.scanner.engines.deployer_fingerprint import (
            fingerprint_deployer,
        )

        result = await fingerprint_deployer(deployer_address=deployer, chain=chain)
        if not result or result.get("result") != "scanned":
            return None

        return {
            "data": result,
            "source": "chain_indexer (ClickHouse deployer behavioral fingerprint)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_deployer_fingerprint failed: %s", exc)
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


async def _chain_indexer_attack_detection(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — C6 time-based attack detection from ClickHouse dex_events.

    Detects sandwich attacks, pump & dumps, and flash loans PURELY from our
    own wallet_memory.dex_events (59M+ rows). No external APIs.

    Parameters (from DataBus fetch kwargs):
      chain: str — chain name or "all" (default "all")
      hours_back: int — scan window in hours (default 1)
      notify: bool — push Redis/Telegram/whale alerts (default true)

    Returns dict with detected attack events + per-stage counts, or None.
    """
    if not PREFER_OWN_DATA:
        return None

    chain = kwargs.get("chain", "all")
    hours_back = int(kwargs.get("hours_back", 1))
    notify = str(kwargs.get("notify", "true")).lower() in ("1", "true", "yes")

    try:
        from app.domains.scanner.engines.attack_detector import run_attack_detection

        result = await run_attack_detection(chain=chain, hours_back=hours_back, notify=notify)
        if not result or result.get("result") != "scanned":
            return None

        return {
            "data": result,
            "source": "chain_indexer (ClickHouse dex_events attack detection)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_attack_detection failed: %s", exc)
        return None


async def _chain_indexer_wallet_flow(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — C1 wallet-to-wallet flow visualization.

    Builds a labeled, USD-denominated fund-flow graph around a wallet from
    evm_transfers + wallet_labels + dex_events (all ClickHouse). Arkham
    replacement — no external APIs.

    Parameters (from DataBus fetch kwargs):
      wallet: str — root wallet address (required)
      address: str — alias for wallet
      chain: str — chain name (required)
      hops: int — traversal depth, 1 or 2 (default 2)
      max_nodes: int — node cap (default 60)

    Returns dict with nodes/edges graph or None.
    """
    if not PREFER_OWN_DATA:
        return None

    wallet = kwargs.get("wallet", kwargs.get("address", "")).lower()
    chain = kwargs.get("chain", "")
    hops = int(kwargs.get("hops", 2))
    max_nodes = int(kwargs.get("max_nodes", 60))

    if not wallet or not chain:
        return None

    try:
        from app.domains.scanner.engines.wallet_flow import build_flow_graph

        result = await build_flow_graph(wallet=wallet, chain=chain, hops=hops, max_nodes=max_nodes)
        if not result or result.get("result") != "scanned" or result.get("edge_count", 0) == 0:
            return None

        return {
            "data": result,
            "source": "chain_indexer (ClickHouse evm_transfers wallet flow)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_wallet_flow failed: %s", exc)
        return None


async def _chain_indexer_new_token_radar(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — C3 new token radar (early warning).

    Polls deployer_events for newly deployed tokens, fast-scans each
    (honeypot + deployer fingerprint + liquidity), scores CRITICAL/HIGH/
    WARNING/INFO, and pushes CRITICAL/HIGH alerts to Telegram.

    Parameters (from DataBus fetch kwargs):
      chain: str — chain name (default ethereum)
      lookback_minutes: int — deployment window (default 5)
      notify: bool — push Telegram alerts (default true)

    Returns dict with radar results or None.
    """
    if not PREFER_OWN_DATA:
        return None

    chain = kwargs.get("chain", "ethereum")
    lookback_minutes = int(kwargs.get("lookback_minutes", 5))
    notify = str(kwargs.get("notify", "true")).lower() in ("1", "true", "yes")

    try:
        from app.domains.scanner.engines.new_token_radar import scan_new_tokens

        result = await scan_new_tokens(chain=chain, lookback_minutes=lookback_minutes, notify=notify)
        if not result:
            return None

        return {
            "data": {
                "results": result,
                "count": len(result),
                "chain": chain,
                "lookback_minutes": lookback_minutes,
            },
            "source": "chain_indexer (ClickHouse deployer_events new token radar)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_new_token_radar failed: %s", exc)
        return None


async def _chain_indexer_hype_score(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — C5 hype score (composite token momentum).

    Combines social velocity (CoinGecko + DexScreener via unified trending),
    own dex_events volume growth, own evm_transfers holder growth, and the
    F2 price oracle momentum into a 0-100 hype score with exponential decay.

    Parameters (from DataBus fetch kwargs):
      token_address: str — token contract address (required)
      address: str — alias for token_address
      chain: str — chain name (default ethereum)

    Returns dict with hype score + signal breakdown or None.
    """
    if not PREFER_OWN_DATA:
        return None

    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = kwargs.get("chain", "ethereum")

    if not token_address:
        return None

    try:
        from app.domains.scanner.engines.hype_score import compute_hype_score

        result = await compute_hype_score(token_address=token_address, chain=chain)
        if not result or result.get("result") != "scanned":
            return None

        return {
            "data": result,
            "source": "chain_indexer (ClickHouse hype score)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_hype_score failed: %s", exc)
        return None


async def _chain_indexer_holder_clusters(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — C2 BubbleMaps-style holder clustering.

    Holder balances computed from evm_transfers deltas (electrs-inspired —
    no separate balance table), three cluster passes (shared funder,
    same-block buy, bundle), batch-labeled via wallet_labels.

    Parameters (from DataBus fetch kwargs):
      token_address: str — token contract address
      address: str — alias for token_address
      chain: str — chain name

    Returns dict with holders/metrics/clusters/concentration or None.
    """
    if not PREFER_OWN_DATA:
        return None

    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = kwargs.get("chain", "")

    if not token_address or not chain:
        return None

    try:
        from app.domains.scanner.engines.holder_clusters import analyze_holder_clusters

        result = await analyze_holder_clusters(
            token_address=token_address,
            chain=chain,
        )
        if not result or result.get("result") != "scanned" or not result.get("holders"):
            return None

        return {
            "data": result,
            "source": "chain_indexer (ClickHouse holder clustering)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("chain_indexer_holder_clusters failed: %s", exc)
        return None
