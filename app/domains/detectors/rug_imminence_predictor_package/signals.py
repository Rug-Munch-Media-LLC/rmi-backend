"""
RIP — Signal Analyzer Modules
Extracted signal analysis logic from RugImminencePredictor.
Each function operates on a RIPResult and updates one signal category.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.detectors.rug_imminence_predictor_package.types import RIPResult

logger = get_logger(__name__)


async def analyze_lp_health(result: RIPResult) -> None:
    """Signal 1: LP Health (25% weight)."""
    sig = result.signals["lp_health"]
    try:
        from app.caching_shield.service_mcp import get_service_mcp

        get_service_mcp()

        dex_data = None
        result.sources_used.append("dexscreener")
        try:
            from app.domains.detectors.rug_imminence_predictor_package.sources import (
                fetch_dexscreener_pool,
            )

            dex_data = await fetch_dexscreener_pool(result.token_address, result.chain)
        except (AttributeError, ImportError):
            result.sources_failed.append("dexscreener")

        if dex_data and dex_data.get("pairs"):
            pair = dex_data["pairs"][0]
            liquidity_usd = (
                float(pair.get("liquidity", {}).get("usd", 0))
                if isinstance(pair.get("liquidity"), dict)
                else float(pair.get("liquidity", 0))
            )
            volume_24h = (
                float(pair.get("volume", {}).get("h24", 0))
                if isinstance(pair.get("volume"), dict)
                else float(pair.get("volume", 0))
            )
            txns_24h = pair.get("txns", {}).get("h24", {}) if isinstance(pair.get("txns"), dict) else {}
            buys = int(txns_24h.get("buys", 0)) if isinstance(txns_24h, dict) else 0
            sells = int(txns_24h.get("sells", 0)) if isinstance(txns_24h, dict) else 0

            if liquidity_usd < 1000:
                sig.score += 40
                sig.flags.append("critically_low_liquidity")
                sig.evidence.append(f"Liquidity ${liquidity_usd:.0f} < $1K - extreme risk")
            elif liquidity_usd < 10000:
                sig.score += 20
                sig.flags.append("low_liquidity")
                sig.evidence.append(f"Liquidity ${liquidity_usd:.0f} < $10K - low")
            elif liquidity_usd > 100000:
                sig.score -= 15
                sig.evidence.append(f"Liquidity ${liquidity_usd:.0f} - healthy")

            if liquidity_usd > 0 and volume_24h > 0:
                vol_liq_ratio = volume_24h / liquidity_usd
                if vol_liq_ratio > 10:
                    sig.score += 15
                    sig.flags.append("high_volume_liquidity_ratio")
                    sig.evidence.append(f"Volume/Liquidity ratio {vol_liq_ratio:.1f}x - potential wash trading")
                elif vol_liq_ratio < 0.1:
                    sig.score += 10
                    sig.flags.append("low_activity")
                    sig.evidence.append("Volume/Liquidity ratio < 0.1x - dead token")

            if buys > 0 and sells > 0:
                sell_buy_ratio = sells / buys
                if sell_buy_ratio > 2:
                    sig.score += 15
                    sig.flags.append("sell_pressure")
                    sig.evidence.append(f"Sell/Buy ratio {sell_buy_ratio:.1f}x - distribution pressure")
                elif sell_buy_ratio < 0.3:
                    sig.score += 5
                    sig.flags.append("buy_pressure")
                    sig.evidence.append(f"Buy pressure dominates ({sell_buy_ratio:.1f}x) - potential manipulation")

            try:
                from app.domains.detectors.rug_imminence_predictor_package.sources import (
                    check_lp_lock,
                )

                lock_info = await check_lp_lock(result.token_address, result.chain)
                if lock_info.get("locked"):
                    sig.score -= 20
                    sig.evidence.append(f"LP locked until {lock_info.get('unlock_date', 'unknown')}")
                elif lock_info.get("unlocked") is True:
                    sig.score += 25
                    sig.flags.append("unlocked_liquidity")
                    sig.evidence.append("LP UNLOCKED - liquidity can be pulled at any time")
                else:
                    sig.score += 10
                    sig.flags.append("unknown_lock_status")
                    sig.evidence.append("LP lock status unknown - assume unlocked")
            except (AttributeError, ImportError):
                sig.score += 5
                result.sources_failed.append("lp_lock_check")
        else:
            sig.score += 15
            sig.evidence.append("No DEX pool data found - token may have no real liquidity")

        sig.confidence = min(1.0, sig.score / 50) if sig.score > 0 else 0.3
    except (KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"LP health analysis failed for {result.token_address}: {e}")
        sig.evidence.append(f"Analysis error: {str(e)[:60]}")
        sig.confidence = 0.1
        result.sources_failed.append("lp_health_analysis")


async def analyze_deployer_risk(result: RIPResult) -> None:
    """Signal 2: Deployer Risk (20% weight)."""
    sig = result.signals["deployer_risk"]
    try:
        from app.domains.detectors.rug_imminence_predictor_package.sources import (
            check_deployer_history,
            check_wallet_age,
            resolve_deployer,
        )

        deployer = await resolve_deployer(result.token_address, result.chain)
        if not deployer:
            sig.score += 10
            sig.evidence.append("Could not resolve deployer address")
            sig.confidence = 0.3
            result.sources_failed.append("deployer_resolution")
            return

        result.sources_used.append("deployer_analysis")
        age_info = await check_wallet_age(deployer, result.chain)
        if age_info:
            sig.evidence.append(f"Deployer wallet created {age_info.get('age_days', '?')} days ago")
            if age_info.get("age_days", 999) < 7:
                sig.score += 25
                sig.flags.append("new_deployer_wallet")
                sig.evidence.append("Deployer wallet < 7 days old - HIGH risk of disposable wallet")
            elif age_info.get("age_days", 999) < 30:
                sig.score += 15
                sig.flags.append("recent_deployer_wallet")
            else:
                sig.score -= 15
                sig.evidence.append("Established deployer wallet (>30 days)")

        history = await check_deployer_history(deployer, result.chain)
        total_tokens = history.get("total_deployed", 0)
        rug_count = history.get("rug_tokens", 0)
        active_tokens = history.get("active_tokens", 0)

        if rug_count > 0:
            sig.score += 30
            sig.flags.append("known_rug_deployer")
            sig.evidence.append(f"Deployer has {rug_count} previous rug pull(s) out of {total_tokens} tokens")
        elif rug_count == 0 and total_tokens > 0:
            sig.score -= 10
            sig.evidence.append(f"Clean deployer history ({total_tokens} tokens, no known rugs)")

        if total_tokens > 5:
            sig.score += 10
            sig.flags.append("serial_deployer")
            sig.evidence.append(f"Deployer created {total_tokens} tokens - potential serial deployer")

        if active_tokens == 0 and total_tokens > 0:
            sig.score += 10
            sig.evidence.append("All previously deployed tokens are inactive/dead")

        sig.confidence = min(0.9, 0.4 + (total_tokens / 20))
    except (KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Deployer risk analysis failed: {e}")
        sig.evidence.append(f"Analysis error: {str(e)[:60]}")
        sig.confidence = 0.2
        result.sources_failed.append("deployer_risk_analysis")


async def analyze_smart_money_flow(result: RIPResult) -> None:
    """Signal 3: Smart Money Flow (15% weight)."""
    sig = result.signals["smart_money_flow"]
    try:
        try:
            from app.smart_money_tracker import SmartMoneyTracker

            tracker = SmartMoneyTracker()
            moves = await tracker.scan_for_token(result.token_address, result.chain, lookback_hours=24)  # type: ignore[attr-defined]
            result.sources_used.append("smart_money_tracker")
            if moves:
                insider_sells = [m for m in moves if m.get("type") == "sell" and m.get("category") == "insider"]
                whale_sells = [m for m in moves if m.get("type") == "sell" and m.get("category") == "whale"]
                deployer_moves = [m for m in moves if m.get("type") == "transfer" and m.get("to_exchange")]
                if insider_sells:
                    total_insider = sum(m.get("value_usd", 0) for m in insider_sells)
                    sig.score += 20
                    sig.flags.append("insider_selling")
                    sig.evidence.append(f"{len(insider_sells)} insider sell(s) detected (${total_insider:,.0f} total)")
                if whale_sells:
                    sig.score += 10
                    sig.flags.append("whale_selling")
                    sig.evidence.append(f"{len(whale_sells)} whale(s) reducing position")
                if deployer_moves:
                    sig.score += 25
                    sig.flags.append("deployer_cashing_out")
                    sig.evidence.append(f"Deployer sent funds to exchange ({len(deployer_moves)} txns)")
        except (ImportError, AttributeError) as e:
            logger.debug(f"Smart money tracker unavailable: {e}")
            result.sources_failed.append("smart_money_tracker")

        try:
            from app.domains.detectors.rug_imminence_predictor_package.sources import (
                fetch_holder_data,
            )

            holder_data = await fetch_holder_data(result.token_address, result.chain)
            if holder_data:
                result.sources_used.append("holder_data")
                prev_count = holder_data.get("holders_24h_ago", 0)
                curr_count = holder_data.get("holders_now", 0)
                if prev_count > 0 and curr_count < prev_count * 0.8:
                    sig.score += 15
                    sig.flags.append("holder_exodus")
                    sig.evidence.append(f"Holders dropped {(1 - curr_count / prev_count) * 100:.0f}% in 24h")
                if holder_data.get("top10_pct", 0) > 80:
                    sig.score += 15
                    sig.flags.append("concentrated_holdings")
                    sig.evidence.append(f"Top 10 holders own {holder_data['top10_pct']:.0f}% of supply")
        except (AttributeError, ImportError):
            result.sources_failed.append("holder_data")

        sig.confidence = min(0.8, 0.3 + (sig.score / 50))
    except (KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Smart money flow analysis failed: {e}")
        sig.evidence.append(f"Analysis error: {str(e)[:60]}")
        sig.confidence = 0.15
        result.sources_failed.append("smart_money_flow_analysis")


async def analyze_bundle_pattern(result: RIPResult) -> None:
    """Signal 4: Bundle Pattern (15% weight)."""
    sig = result.signals["bundle_pattern"]
    try:
        from app.bundle_detector import BundleDetector

        detector = BundleDetector()
        detection = await detector.detect(result.token_address, result.chain)
        result.sources_used.append("bundle_detector")
        if detection.is_bundled:
            sig.score += detection.confidence * 50
            sig.flags.append("bundled_launch")
            sig.evidence.append(f"Bundle detected (confidence: {detection.confidence:.0%})")
            if detection.common_funder_score > 0.7:
                sig.score += 10
                sig.evidence.append("All initial buyers funded by same wallet")
            if detection.top10_holder_pct > 90:
                sig.score += 10
                sig.evidence.append(f"Top 10 holders: {detection.top10_holder_pct:.0f}% - extreme concentration")
            if detection.identical_amount_count > 3:
                sig.score += 10
                sig.evidence.append(f"{detection.identical_amount_count} identical purchases - coordinated")
        else:
            sig.score -= 10
            sig.evidence.append("No bundle patterns detected")
        sig.confidence = detection.confidence if detection.is_bundled else 0.6
    except ImportError:
        logger.debug("BundleDetector not available")
        sig.evidence.append("Bundle detection unavailable")
        sig.confidence = 0.3
        result.sources_failed.append("bundle_detector")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("analyze_bundle_pattern_error", exc_info=True)
        logger.warning(f"Bundle analysis failed: {e}")
        sig.evidence.append(f"Analysis error: {str(e)[:60]}")
        sig.confidence = 0.2
        result.sources_failed.append("bundle_pattern_analysis")


async def analyze_social_velocity(result: RIPResult) -> None:
    """Signal 5: Social Velocity (10% weight)."""
    sig = result.signals["social_velocity"]
    try:
        try:
            from app.domains.scanners.social_velocity import SocialVelocityAnalyzer

            analyzer = SocialVelocityAnalyzer()
            velocity = await analyzer.analyze([])  # no mention feed wired up; see [REVIEW]
            result.sources_used.append("social_velocity_analyzer")
            if velocity:
                mention_spike = velocity.get("mention_spike_pct", 0)  # type: ignore[attr-defined]
                shill_score = velocity.get("shill_score", 0)  # type: ignore[attr-defined]
                sentiment_change = velocity.get("sentiment_shift", 0)  # type: ignore[attr-defined]
                if mention_spike > 500:
                    sig.score += 15
                    sig.flags.append("massive_mention_spike")
                    sig.evidence.append(f"Social mentions spiked {mention_spike:.0f}% - suspicious coordinated hype")
                elif mention_spike > 200:
                    sig.score += 8
                    sig.flags.append("mention_spike")
                    sig.evidence.append(f"Social mentions up {mention_spike:.0f}%")
                if shill_score > 0.7:
                    sig.score += 10
                    sig.flags.append("coordinated_shilling")
                    sig.evidence.append("Coordinated shilling detected (same messages across channels)")
                if sentiment_change < -0.3:
                    sig.score += 10
                    sig.flags.append("sentiment_crash")
                    sig.evidence.append(f"Sentiment dropped {sentiment_change:.0%}")
        except (ImportError, AttributeError) as e:
            logger.debug(f"Social velocity analyzer unavailable: {e}")
            result.sources_failed.append("social_velocity_analyzer")

        try:
            from app.domains.scanners.sentiment_analyzer import SentimentAnalyzer

            sentiment = SentimentAnalyzer()
            sent_result = await sentiment.analyze_token_sentiment(result.token_symbol or result.token_address)  # type: ignore[attr-defined]
            result.sources_used.append("sentiment_analyzer")
            if sent_result and sent_result.get("fear_index", 0) > 70:
                sig.score += 5
                sig.flags.append("high_fear")
                sig.evidence.append("High fear sentiment in community")
        except (ImportError, AttributeError):
            result.sources_failed.append("sentiment_analyzer")

        sig.confidence = min(0.7, 0.3 + (sig.score / 40))
    except (KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Social velocity analysis failed: {e}")
        sig.evidence.append(f"Analysis error: {str(e)[:60]}")
        sig.confidence = 0.15
        result.sources_failed.append("social_velocity_analysis")


async def analyze_contract_risk(result: RIPResult) -> None:
    """Signal 6: Contract Risk (10% weight)."""
    sig = result.signals["contract_risk"]
    try:
        try:
            from app.domains.scanners.honeypot_detector import HoneypotDetector

            hp = HoneypotDetector()
            hp_result = await hp.detect(result.token_address, result.chain)
            result.sources_used.append("honeypot_detector")
            if hp_result and hp_result.get("is_honeypot", False):  # type: ignore[attr-defined]
                sig.score += 30
                sig.flags.append("honeypot")
                sig.evidence.append(f"HONEYPOT DETECTED: {hp_result.get('reason', 'Cannot sell')}")  # type: ignore[attr-defined]
            elif hp_result and hp_result.get("suspicious", False):  # type: ignore[attr-defined]
                sig.score += 15
                sig.flags.append("suspicious_contract")
                sig.evidence.append(f"Suspicious contract behavior: {hp_result.get('reason', '')}")  # type: ignore[attr-defined]
            else:
                sig.score -= 10
                sig.evidence.append("No honeypot detected")
        except (ImportError, AttributeError):
            result.sources_failed.append("honeypot_detector")

        try:
            from app.domains.scanners.contract_authority import ContractAuthorityScanner

            auth = ContractAuthorityScanner()
            authority = await auth.scan(result.token_address, result.chain)
            result.sources_used.append("contract_authority")
            if authority:
                if authority.get("ownership_renounced", False):  # type: ignore[attr-defined]
                    sig.score -= 15
                    sig.evidence.append("Ownership renounced - lower risk")
                else:
                    sig.score += 15
                    sig.flags.append("active_ownership")
                    sig.evidence.append("Ownership NOT renounced - owner can manipulate")
                if authority.get("has_proxy", False):  # type: ignore[attr-defined]
                    sig.score += 10
                    sig.flags.append("upgradable_proxy")
                    sig.evidence.append("Upgradable proxy contract - implementation can change")
        except (ImportError, AttributeError):
            result.sources_failed.append("contract_authority")

        sig.confidence = min(0.85, 0.4 + (sig.score / 40))
    except (KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Contract risk analysis failed: {e}")
        sig.evidence.append(f"Analysis error: {str(e)[:60]}")
        sig.confidence = 0.2
        result.sources_failed.append("contract_risk_analysis")


async def analyze_liquidity_migration(result: RIPResult) -> None:
    """Signal 7: Liquidity Migration (5% weight)."""
    sig = result.signals["liquidity_migration"]
    try:
        try:
            from app.domains.scanners.liquidity_verifier import LiquidityVerifier

            verifier = LiquidityVerifier()
            lp_status = await verifier.verify(result.token_address, result.chain, lookback_hours=24)  # type: ignore[attr-defined]
            result.sources_used.append("liquidity_verifier")
            if lp_status:
                if lp_status.get("lp_removed", False):
                    sig.score += 30
                    sig.flags.append("lp_removed")
                    amount = lp_status.get("amount_removed_usd", 0)
                    sig.evidence.append(f"LP REMOVED! ${amount:,.0f} withdrawn in last 24h")
                elif lp_status.get("lp_decreased", False):
                    sig.score += 20
                    sig.flags.append("lp_decreasing")
                    sig.evidence.append("LP decreased significantly in last 24h")
                if lp_status.get("single_sided_add", False):
                    sig.score += 10
                    sig.flags.append("single_sided_lp")
                    sig.evidence.append("Single-sided LP addition - potential manipulation")
                if lp_status.get("lp_to_cex", False):
                    sig.score += 25
                    sig.flags.append("lp_to_exchange")
                    sig.evidence.append("LP tokens transferred to CEX - preparing to exit")
            else:
                sig.evidence.append("No recent LP migration detected")
        except (ImportError, AttributeError):
            result.sources_failed.append("liquidity_verifier")

        if not sig.evidence:
            try:
                from app.domains.detectors.rug_imminence_predictor_package.sources import (
                    fetch_dexscreener_pool,
                )

                dex_data = await fetch_dexscreener_pool(result.token_address, result.chain)
                if dex_data and dex_data.get("pairs"):
                    pair = dex_data["pairs"][0]
                    liq_change = (
                        pair.get("liquidity", {}).get("change_24h", 0) if isinstance(pair.get("liquidity"), dict) else 0
                    )
                    if liq_change < -50:
                        sig.score += 15
                        sig.flags.append("liquidity_drop")
                        sig.evidence.append(f"Liquidity dropped {abs(liq_change):.0f}% in 24h")
            except (AttributeError, ImportError):
                logger.debug("liquidity change parse failed", exc_info=True)

        sig.confidence = min(0.7, 0.3 + (sig.score / 40))
    except (KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Liquidity migration analysis failed: {e}")
        sig.evidence.append(f"Analysis error: {str(e)[:60]}")
        sig.confidence = 0.15
        result.sources_failed.append("liquidity_migration_analysis")


# Flag-to-warning/recommendation maps
FLAG_WARNINGS = {
    "honeypot": "⚠️ HONEYPOT: Token cannot be sold - users are trapped",
    "unlocked_liquidity": "⚠️ LP UNLOCKED: Creator can pull all liquidity at any moment",
    "lp_removed": "⚠️ LP REMOVED: Liquidity has been withdrawn - imminent rug",
    "known_rug_deployer": "⚠️ KNOWN RUG DEPLOYER: This wallet has rugged before",
    "deployer_cashing_out": "⚠️ DEPLOYER CASHING OUT: Sending funds to exchanges",
    "insider_selling": "⚠️ INSIDER SELLING: Team/insiders are dumping",
    "bundled_launch": "⚠️ BUNDLED LAUNCH: Coordinated token launch",
    "coordinated_shilling": "⚠️ COORDINATED SHILLING: Artificial social hype",
    "active_ownership": "⚠️ ACTIVE OWNERSHIP: Owner can modify contract",
    "upgradable_proxy": "⚠️ UPGRADABLE PROXY: Contract can be changed",
    "new_deployer_wallet": "⚠️ NEW DEPLOYER: Disposable wallet, < 7 days old",
    "single_sided_lp": "⚠️ SINGLE-SIDED LP: Potential price manipulation",
    "lp_to_exchange": "⚠️ LP TO CEX: Liquidity tokens sent to exchange",
    "sell_pressure": "⚠️ SELL PRESSURE: More sells than buys",
    "massive_mention_spike": "⚠️ HYPE SPIKE: Social mentions up 500%+ - suspicious",
    "serial_deployer": "⚠️ SERIAL DEPLOYER: Created 5+ tokens",
    "concentrated_holdings": "⚠️ CONCENTRATED HOLDINGS: Top 10 own >80% of supply",
    "holder_exodus": "⚠️ HOLDER EXODUS: Large drop in holder count",
}

FLAG_RECOMMENDATIONS = {
    "unlocked_liquidity": "Sell immediately if holding - LP can be pulled any moment",
    "lp_removed": "EXIT NOW - liquidity has been withdrawn",
    "honeypot": "Do not buy - cannot sell, you will lose funds",
    "known_rug_deployer": "Avoid this token - creator has rugged before",
    "deployer_cashing_out": "Consider selling - team is exiting",
    "insider_selling": "Reduce position - insiders are dumping",
    "bundled_launch": "Do NOT trust the holder distribution - it's fabricated",
    "active_ownership": "Monitor owner wallet for mint/blacklist transactions",
    "upgradable_proxy": "Watch for implementation changes - could become a honeypot",
    "coordinated_shilling": "Ignore FOMO - the hype is artificial",
    "new_deployer_wallet": "Wait 30+ days before considering investment",
    "sell_pressure": "Do not buy into distribution",
    "massive_mention_spike": "Ignore sudden hype - likely a coordinated pump",
    "concentrated_holdings": "High risk of price manipulation by top holders",
    "holder_exodus": "Smart money is leaving - follow them",
}

PRIORITY_FLAGS = ["honeypot", "lp_removed", "unlocked_liquidity", "deployer_cashing_out"]


def generate_warnings(result: RIPResult) -> None:
    """Generate human-readable warnings from flags."""
    seen = set()
    for sig in result.signals.values():
        for flag in sig.flags:
            if flag in FLAG_WARNINGS and flag not in seen:
                result.warnings.append(FLAG_WARNINGS[flag])
                seen.add(flag)
    if not result.warnings:
        if result.score < 30:
            result.warnings.append("✅ No imminent rug indicators detected")
        else:
            result.warnings.append("🔍 Some risk signals present - monitor closely")


def generate_recommendations(result: RIPResult) -> None:
    """Generate actionable recommendations from flags."""
    for sig_name in ["lp_health", "contract_risk", "smart_money_flow"]:
        sig = result.signals.get(sig_name)
        if sig:
            for flag in PRIORITY_FLAGS:
                if flag in sig.flags:
                    result.recommendations.insert(0, f"🔴 URGENT - {FLAG_RECOMMENDATIONS.get(flag, 'Exit position')}")

    for sig in result.signals.values():
        for flag in sig.flags:
            if flag in FLAG_RECOMMENDATIONS and FLAG_RECOMMENDATIONS[flag] not in result.recommendations:
                result.recommendations.append(f"• {FLAG_RECOMMENDATIONS[flag]}")

    if not result.recommendations:
        if result.score < 30:
            result.recommendations.append("✅ No urgent action needed - low risk")
        elif result.score < 55:
            result.recommendations.append("📊 Monitor token closely for the next 24-48 hours")
        else:
            result.recommendations.append("⚠️ Consider reducing exposure")
