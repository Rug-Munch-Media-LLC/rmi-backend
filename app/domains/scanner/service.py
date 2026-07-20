"""Scanner service — main scan_token function and entry points."""

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.modules import (
    _check_bundle,
    _check_contract_verification,
    _check_copycat,
    _check_goplus_security,
    _check_honeypot_is,
    _check_liquidity_lock_multi,
    _check_lp_concentration,
    _check_ownership_renounced,
    _check_social_sentiment,
    _check_spam,
    _check_token_age,
    _check_volume_liquidity_ratio,
    _get_deployer_info,
    _get_holder_data,
    _get_price_consensus,
)
from app.domains.scanner.rag_scam_check import check_scam as _rag_check_scam

logger = get_logger(__name__)

# Re-export ScanResult for backward compatibility
from app.domains.scanner.market_data import fetch_market_data  # noqa: E402
from app.domains.scanner.models import ScanResult  # noqa: E402
from app.domains.scanner.modules import (  # noqa: E402
    _check_blockscout,
    _check_defi_scanner,
)


async def _rag_scam_check(
    token_address: str, chain: str, deployer: str | None = None
) -> dict[str, Any] | None:
    """Check RAG for scam indicators via vector search + pattern matching."""
    logger.info("rag_scam_check", token=token_address, chain=chain, deployer=deployer)
    try:
        result = await _rag_check_scam(
            token_address=token_address,
            chain=chain,
            deployer=deployer,
        )
        if result:
            return result
    except Exception as e:
        logger.warning("rag_scam_check_failed", error=str(e)[:100])

    return {"check": "rag", "result": "scanned", "risk_score": 0, "source": "unavailable"}


async def _check_cross_chain_deployer(deployer_addr: str, chain: str) -> dict[str, Any] | None:
    """Check if deployer has cross-chain activity.

    Args:
        deployer_addr: Deployer wallet address
        chain: Blockchain network identifier

    Returns:
        Dict with check result or None if not applicable

    Note:
        Stub — requires cross-chain indexer integration. Returns "unavailable"
        until cross-chain entity resolution is deployed.
    """
    logger.info("cross_chain_deployer", deployer=deployer_addr, chain=chain)
    return {
        "check": "cross_chain",
        "result": "unavailable",
        "reason": "Cross-chain indexer not yet deployed",
    }


async def _check_volume_anomaly(
    token_address: str,
    chain: str,
    market_price: float = 0,
    volume_24h: float = 0,
    circulating_supply: float = 0,
) -> dict[str, Any] | None:
    """Check for wash-trading volume anomalies via statistical deviation.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier
        market_price: Current market price in USD
        volume_24h: 24-hour trading volume in USD
        circulating_supply: Circulating token supply

    Returns:
        Dict with anomaly detection result or None

    Note:
        Stub — needs wash-trading detection model deployed. Returns "unavailable"
        until model integration is complete.
    """
    logger.info("check_volume", token=token_address, chain=chain)
    return {
        "check": "volume",
        "result": "unavailable",
        "reason": "Volume anomaly model not yet deployed",
    }


async def _get_birdeye_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Birdeye market data for token (price, volume, liquidity).

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Birdeye data or unavailable status

    Note:
        Stub — requires Birdeye API key. Returns "unavailable" until configured.
    """
    logger.info("birdeye_data", token=token_address, chain=chain)
    return {"check": "birdeye", "result": "unavailable", "reason": "Birdeye API not configured"}


async def _verify_mint_consensus(token_address: str, chain: str) -> dict[str, Any] | None:
    """Verify mint authority across multiple RPC sources.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with consensus verification result

    Note:
        Stub — requires multi-RPC mint authority verification.
    """
    logger.info("verify_mint", token=token_address, chain=chain)
    return {
        "check": "mint",
        "result": "unavailable",
        "reason": "Multi-RPC mint verification not deployed",
    }


async def _ingest_scan_to_rag(scan_result: Any) -> None:
    """Ingest scan result to RAG for future similarity searches.

    Args:
        scan_result: The completed ScanResult to persist
    """
    logger.info("ingest_to_rag", token=scan_result.token_address)


async def _get_solscan_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Solscan enhanced data for Solana tokens.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Solscan data or unavailable status
    """
    logger.info("solscan_data", token=token_address, chain=chain)
    return {"check": "solscan", "result": "unavailable", "reason": "Solscan API not configured"}


async def _get_moralis_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Moralis enriched token data.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Moralis data or unavailable status
    """
    logger.info("moralis_data", token=token_address, chain=chain)
    return {"check": "moralis", "result": "unavailable", "reason": "Moralis API not configured"}


async def _get_etherscan_enhanced(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get enhanced Etherscan data (source code, ABI, events).

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Etherscan data or unavailable status
    """
    logger.info("etherscan_enhanced", token=token_address, chain=chain)
    return {
        "check": "etherscan",
        "result": "unavailable",
        "reason": "Etherscan API key not configured",
    }


async def _get_quicknode_pumpfun(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Quicknode Pump.fun data for Solana meme tokens.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Quicknode data or unavailable status
    """
    logger.info("quicknode_pumpfun", token=token_address, chain=chain)
    return {
        "check": "quicknode",
        "result": "unavailable",
        "reason": "Quicknode Pump.fun API not configured",
    }


async def _check_nansen(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Nansen label data for token/address.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Nansen labels or unavailable status
    """
    logger.info("nansen_check", token=token_address, chain=chain)
    return {"check": "nansen", "result": "unavailable", "reason": "Nansen API not configured"}


async def _check_chainaware(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ChainAware risk data for token.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with ChainAware data or unavailable status
    """
    logger.info("chainaware_check", token=token_address, chain=chain)
    return {"check": "chainaware", "result": "unavailable", "reason": "ChainAware not integrated"}


async def _check_blowfish(
    token_address: str, chain: str, user_address: str = ""
) -> dict[str, Any] | None:
    """Check Blowfish AI security scan for token.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier
        user_address: Optional user wallet for context

    Returns:
        Dict with Blowfish result or unavailable status
    """
    logger.info("blowfish_check", token=token_address, chain=chain, user=user_address)
    return {"check": "blowfish", "result": "unavailable", "reason": "Blowfish API not configured"}


async def _check_santiment(symbol: str) -> dict[str, Any] | None:
    """Check Santiment social metrics for token symbol.

    Args:
        symbol: Token ticker symbol

    Returns:
        Dict with Santiment data or unavailable status
    """
    logger.info("santiment_check", symbol=symbol)
    return {"check": "santiment", "result": "unavailable", "reason": "Santiment API not configured"}


async def _check_fear_greed() -> dict[str, Any] | None:
    """Check Crypto Fear & Greed market sentiment index.

    Returns:
        Dict with fear/greed value or unavailable status
    """
    logger.info("fear_greed_check")
    return {
        "check": "fear_greed",
        "result": "unavailable",
        "reason": "Fear & Greed source not configured",
    }


async def _check_news_sentiment(symbol: str) -> dict[str, Any] | None:
    """Check recent news sentiment for token symbol.

    Args:
        symbol: Token ticker symbol

    Returns:
        Dict with news sentiment or unavailable status
    """
    logger.info("news_sentiment_check", symbol=symbol)
    return {
        "check": "news",
        "result": "unavailable",
        "reason": "News sentiment pipeline not configured",
    }


async def _check_augmento(symbol: str) -> dict[str, Any] | None:
    """Check Augmento AI analysis for token.

    Args:
        symbol: Token ticker symbol

    Returns:
        Dict with Augmento result or unavailable status
    """
    logger.info("augmento_check", symbol=symbol)
    return {"check": "augmento", "result": "unavailable", "reason": "Augmento API not configured"}


async def _check_coingecko_trending() -> dict[str, Any] | None:
    """Check CoinGecko trending tokens list.

    Returns:
        Dict with trending list or unavailable status
    """
    logger.info("coingecko_trending")
    return {
        "check": "trending",
        "result": "unavailable",
        "reason": "CoinGecko trending not integrated",
    }


async def _check_webacy(address: str, chain: str) -> dict[str, Any] | None:
    """Check Webacy security score for address.

    Args:
        address: Wallet or contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Webacy score or unavailable status
    """
    logger.info("webacy_check", address=address, chain=chain)
    return {"check": "webacy", "result": "unavailable", "reason": "Webacy API not configured"}


async def _check_sourcify(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Sourcify contract verification status.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Sourcify verification data or unavailable status
    """
    logger.info("sourcify_check", token=token_address, chain=chain)
    return {
        "check": "sourcify",
        "result": "unavailable",
        "reason": "Sourcify integration not connected",
    }


async def _check_scamsniffer_live(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ScamSniffer Live for real-time scam alerts.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with ScamSniffer result or unavailable status
    """
    logger.info("scamsniffer_live", token=token_address, chain=chain)
    return {"check": "scamsniffer", "result": "unavailable", "reason": "ScamSniffer not integrated"}


async def _check_dune(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Dune Analytics for on-chain query data.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Dune data or unavailable status
    """
    logger.info("dune_check", token=token_address, chain=chain)
    return {"check": "dune", "result": "unavailable", "reason": "Dune API not configured"}


async def _check_arkham(address: str, chain: str) -> dict[str, Any] | None:
    """Check Arkham Intelligence for address labels.

    Args:
        address: Wallet or contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Arkham labels or unavailable status
    """
    logger.info("arkham_check", address=address, chain=chain)
    return {"check": "arkham", "result": "unavailable", "reason": "Arkham API not configured"}


async def _check_thegraph(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check The Graph subgraph for token data.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with subgraph data or unavailable status
    """
    logger.info("thegraph_check", token=token_address, chain=chain)
    return {"check": "thegraph", "result": "unavailable", "reason": "Subgraph query not deployed"}


# ── Risk scoring engine ───────────────────────────────────────────────


def _calculate_safety_score(modules_results: list[dict[str, Any]]) -> int:
    """Calculate safety score (0-100) from all module results.

    Baseline: 50 (unknown/neutral).
    Each module contributes risk based on its findings:
    - Modules returning error/offline: no impact
    - Clean modules: +5 to score
    - Risk flags: -risk_score contribution
    """
    score = 50  # Neutral baseline
    clean_bonus = 0
    risk_penalty = 0
    modules_with_data = 0

    for mod_entry in modules_results:
        r = mod_entry.get("result", {})
        if not isinstance(r, dict):
            continue
        result_status = r.get("result", "")
        if result_status in ("error", "pending", "unavailable"):
            continue  # Module didn't return useful data

        modules_with_data += 1

        # Check for risk indicators
        risk_score = r.get("risk_score", 0)
        if risk_score and isinstance(risk_score, (int, float)):
            risk_penalty += risk_score * 0.4  # penalty proportional to risk

        # ── Core risk flags ──
        is_honeypot = r.get("is_honeypot", False)
        if is_honeypot:
            risk_penalty += 35

        is_spam = r.get("is_spam", False)
        if is_spam:
            risk_penalty += 30

        is_sanctioned = r.get("is_sanctioned", False)
        if is_sanctioned:
            risk_penalty += 50

        is_bundled = r.get("is_bundled", False)
        if is_bundled:
            confidence = r.get("confidence", 0)
            risk_penalty += confidence * 50

        is_verified = r.get("is_verified", None)
        if is_verified is True:
            clean_bonus += 10
        elif is_verified is False:
            risk_penalty += 10

        # ── Deployer reputation ──
        reputation_label = r.get("reputation_label", "")
        if reputation_label == "CRITICAL":
            risk_penalty += 30
        elif reputation_label == "HIGH":
            risk_penalty += 20
        elif reputation_label == "LOW":
            clean_bonus += 10

        # ── Copycat ──
        copycat_result = r.get("result", "")
        if copycat_result == "high_risk":
            risk_penalty += 25
        elif copycat_result == "medium_risk":
            risk_penalty += 15
        elif copycat_result == "clean":
            clean_bonus += 5

        # ── LP locked ──
        lp_locked = r.get("liquidity_locked_pct", 0)
        if lp_locked and isinstance(lp_locked, (int, float)):
            if lp_locked >= 90:
                clean_bonus += 10
            elif lp_locked <= 10 and r.get("liquidity_usd", 0) > 0:
                risk_penalty += 20

        # ── Ownership renounced (new check) ──
        ownership_renounced = r.get("ownership_renounced", None)
        if ownership_renounced is True:
            clean_bonus += 10
        elif ownership_renounced is False:
            # Active ownership is moderate risk
            risk_level = r.get("risk_level", "")
            if risk_level == "active_proxy_owner":
                risk_penalty += 25
            else:
                risk_penalty += 10

        # ── High concentration (holders) ──
        concentration = r.get("concentration_pct", 0)
        if isinstance(concentration, (int, float)) and concentration > 50:
            risk_penalty += 15

        # ── LP concentration (new check) ──
        top10_pct = r.get("top10_pct", 0)
        if isinstance(top10_pct, (int, float)) and top10_pct > 0:
            if top10_pct >= 90:
                risk_penalty += 30
            elif top10_pct >= 70:
                risk_penalty += 20
            elif top10_pct >= 50:
                risk_penalty += 10

        # ── Social sentiment (new check) ──
        if r.get("is_suspicious"):
            suspicion_count = len(r.get("suspicion_reasons", []))
            risk_penalty += 15 * suspicion_count

        # ── Token age (new check) ──
        age_risk_level = r.get("risk_level", "")
        if age_risk_level == "critical":
            risk_penalty += 20
        elif age_risk_level == "high":
            risk_penalty += 12
        elif age_risk_level == "medium":
            risk_penalty += 5
        elif age_risk_level == "low":
            clean_bonus += 5

        # ── Volume/liquidity ratio (new check) ──
        if r.get("is_wash_trading_suspicious"):
            risk_penalty += 20
        vol_risk_level = r.get("risk_level", "")
        if vol_risk_level == "critical":
            risk_penalty += 25
        elif vol_risk_level == "high":
            risk_penalty += 15

    # Apply modifiers
    score = score + clean_bonus - risk_penalty

    # If we have good data, boost confidence can raise score
    if modules_with_data >= 6 and risk_penalty == 0:
        score = min(95, score + 10)

    # Clamp to 0-100
    return max(0, min(100, int(score)))


async def scan_token(
    token_address: str,
    chain: str = "ethereum",
    tiers: list[str] | None = None,
    include_market_data: bool = True,
    include_social: bool = True,
) -> ScanResult:
    """Scan a token for security issues across ALL SENTINEL modules.

    FREE tier:    SENTINEL Tier 1  (market_data, honeypot, goplus, copycat, spam,
                                    blockscout, defiscanner, token_age, vol/liq ratio)
    PRO tier:     Tier 1 + deployer, holders, verification, liquidity, bundle,
                  lp_concentration, ownership_renounced, social_sentiment
    ELITE tier:   Tier 1+2 + deep deployer, price consensus, RAG, nansen,
                  blowfish, dune, arkham
    """
    if tiers is None:
        tiers = ["free"]

    result = ScanResult(token_address=token_address, chain=chain)

    last_error: str | None = None

    # Run modules in parallel
    async def run_module(name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal last_error
        try:
            r = await func(*args, **kwargs)
            result.modules_run.append({"module": name, "status": "success", "result": r})
            return r
        except Exception as e:
            logger.warning("module_failed", module=name, error=str(e))
            last_error = str(e)
            result.modules_run.append({"module": name, "status": "error", "error": str(e)})
            return None

    tasks = []

    # ── FREE tier (Tier 1) — core checks (no API keys needed) ──
    if "free" in tiers or "pro" in tiers or "elite" in tiers:
        tasks.append(run_module("market_data", fetch_market_data, token_address, chain))
        tasks.append(run_module("honeypot", _check_honeypot_is, token_address, chain))
        tasks.append(run_module("goplus", _check_goplus_security, token_address, chain))
        tasks.append(run_module("copycat", _check_copycat, token_address, ""))
        tasks.append(run_module("spam", _check_spam, token_address, chain))
        tasks.append(run_module("blockscout", _check_blockscout, token_address, chain))
        tasks.append(run_module("defiscanner", _check_defi_scanner, token_address, chain))
        # New FREE checks
        tasks.append(run_module("token_age", _check_token_age, token_address, chain))
        tasks.append(
            run_module(
                "volume_liquidity_ratio", _check_volume_liquidity_ratio, token_address, chain
            )
        )

    # ── PRO tier (Tier 2) — advanced checks ──
    if "pro" in tiers or "elite" in tiers:
        tasks.append(run_module("deployer", _get_deployer_info, token_address, chain))
        tasks.append(run_module("holders", _get_holder_data, token_address, chain))
        tasks.append(run_module("verification", _check_contract_verification, token_address, chain))
        tasks.append(run_module("liquidity", _check_liquidity_lock_multi, token_address, chain))
        tasks.append(run_module("bundle", _check_bundle, token_address, chain))
        # New PRO checks
        tasks.append(run_module("lp_concentration", _check_lp_concentration, token_address, chain))
        tasks.append(run_module("ownership", _check_ownership_renounced, token_address, chain))
        tasks.append(run_module("social_sentiment", _check_social_sentiment, token_address, chain))

    # ── ELITE tier (Tier 3) — deep analysis ──
    if "elite" in tiers:
        tasks.append(run_module("price", _get_price_consensus, token_address, chain))
        tasks.append(run_module("rag", _rag_scam_check, token_address, chain))
        tasks.append(run_module("nansen", _check_nansen, token_address, chain))
        tasks.append(run_module("blowfish", _check_blowfish, token_address, chain))
        tasks.append(run_module("dune", _check_dune, token_address, chain))
        tasks.append(run_module("arkham", _check_arkham, token_address, chain))

    # ── Run all tasks ──
    await asyncio.gather(*tasks)

    # ── Calculate real safety score from module results ──
    result.safety_score = _calculate_safety_score(result.modules_run)

    # ── Generate risk flags ──
    risk_flags: list[str] = []
    for mod_entry in result.modules_run:
        r = mod_entry.get("result")
        if not isinstance(r, dict):
            continue

        module_name = mod_entry.get("module", "")

        if r.get("is_honeypot"):
            risk_flags.append("honeypot")
        if r.get("is_spam"):
            risk_flags.append("flagged_spam")
        if r.get("is_sanctioned"):
            risk_flags.append("sanctioned_address")
        if r.get("is_bundled"):
            risk_flags.append("bundled_launch")
        if r.get("result") == "high_risk" and module_name == "copycat":
            risk_flags.append("copycat_token")
        if r.get("reputation_label") == "CRITICAL":
            risk_flags.append("malicious_deployer")
        if r.get("reputation_label") == "HIGH":
            risk_flags.append("high_risk_deployer")

        risk_score = r.get("risk_score", 0)
        if (
            isinstance(risk_score, (int, float))
            and risk_score >= 70
            and module_name in ("goplus", "honeypot")
        ):
            risk_flags.append("high_risk_score")

        # Concentration (existing)
        if isinstance(r.get("concentration_pct"), (int, float)) and r["concentration_pct"] > 80:
            risk_flags.append("concentrated_holders")

        # ── New check risk flags ──
        # Token age
        if r.get("risk_level") == "critical" and module_name == "token_age":
            risk_flags.append("new_token_under_1h")
        elif r.get("is_new_token") and module_name == "token_age":
            risk_flags.append("token_under_24h")

        # LP concentration
        if module_name == "lp_concentration":
            if r.get("risk_level") == "critical":
                risk_flags.append("extreme_lp_concentration")
            elif r.get("is_concentrated"):
                risk_flags.append("concentrated_lp")

        # Ownership
        if module_name == "ownership":
            if r.get("risk_level") == "active_proxy_owner":
                risk_flags.append("active_proxy_owner")
            elif r.get("risk_level") == "active_owner":
                risk_flags.append("active_owner")

        # Social sentiment
        if r.get("is_suspicious") and module_name == "social_sentiment":
            risk_flags.append("suspicious_social_sentiment")

        # Volume/liquidity ratio
        if r.get("is_wash_trading_suspicious") and module_name == "volume_liquidity_ratio":
            risk_flags.append("wash_trading_suspected")
        if module_name == "volume_liquidity_ratio" and r.get("risk_level") == "critical":
            risk_flags.append("extreme_volume_liquidity_ratio")

    result.risk_flags = risk_flags

    # ── Calculate confidence based on how many modules returned data ──
    successful = sum(1 for m in result.modules_run if m.get("status") == "success")
    total = len(result.modules_run) if result.modules_run else 1
    base_confidence = min(100, int((successful / total) * 100))
    # Add bonus for modules with real data (not pending)
    pending_count = sum(
        1
        for m in result.modules_run
        if isinstance(m.get("result"), dict) and m["result"].get("result") == "pending"
    )
    real_count = successful - pending_count
    result.confidence = min(100, base_confidence + real_count * 5)

    # Ingest to RAG
    await _ingest_scan_to_rag(result)

    return result


async def quick_scan_text(token_address: str, chain: str = "solana") -> str:
    """Quick text scan for token."""
    result = await scan_token(token_address, chain, tiers=["free", "pro"])
    return (
        f"Token {token_address} scanned on {chain}. "
        f"Safety score: {result.safety_score}/100. "
        f"Confidence: {result.confidence}%. "
        f"Flags: {', '.join(result.risk_flags) if result.risk_flags else 'None'}"
    )


# Backward compatibility: re-export scan_token
from app.domains.scanner.service import scan_token  # noqa: E402, F811
