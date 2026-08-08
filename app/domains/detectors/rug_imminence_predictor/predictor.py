"""Main Rug Imminence Predictor class."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.domains.detectors.rug_imminence_predictor.config import (
    DEFAULT_WEIGHTS,
    SCORE_THRESHOLDS,
    SIGNAL_NAMES,
)
from app.domains.detectors.rug_imminence_predictor.models import (
    RIPResult,
    SignalCategory,
)

logger = get_logger(__name__)


class RugImminencePredictor:
    """Predicts imminent rug pulls by fusing 7 signal categories."""

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self._verify_weights()
        self._cache: dict[str, RIPResult] = {}
        self._cache_ttl = 300
        logger.info(f"RugImminencePredictor initialized with {len(self.weights)} signals")

    def _verify_weights(self) -> None:
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            logger.warning(f"Signal weights sum to {total:.2f}, not 1.0. Normalizing.")
            for k in self.weights:
                self.weights[k] /= total

    def _cache_key(self, address: str, chain: str) -> str:
        return f"{chain}:{address.lower()}"

    def _get_cached(self, address: str, chain: str) -> RIPResult | None:
        key = self._cache_key(address, chain)
        result = self._cache.get(key)
        if result:
            try:
                expiry = datetime.fromisoformat(result.last_updated)
                if datetime.now(UTC) < expiry:
                    return result
            except (ValueError, TypeError):
                pass
            del self._cache[key]
        return None

    def _set_cache(self, result: RIPResult) -> None:
        key = self._cache_key(result.token_address, result.chain)
        result.last_updated = (datetime.now(UTC) + timedelta(seconds=self._cache_ttl)).isoformat()
        self._cache[key] = result
        if len(self._cache) > 500:
            to_evict = sorted(self._cache.items(), key=lambda x: x[1].last_updated)[: len(self._cache) // 4]
            for k, _ in to_evict:
                del self._cache[k]
            logger.info(f"Cache eviction: removed {len(to_evict)} entries, {len(self._cache)} remaining")

    async def predict(self, address: str, chain: str = "base", force_refresh: bool = False) -> RIPResult:
        address = address.strip()
        chain = chain.strip().lower()
        valid_chains = {
            "base",
            "solana",
            "ethereum",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "avalanche",
            "fantom",
            "gnosis",
        }
        if chain not in valid_chains:
            raise ValueError(f"Unsupported chain '{chain}'. Supported: {', '.join(sorted(valid_chains))}")
        if not re.match(r"^0x[a-fA-F0-9]{40}$", address) and not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address):
            raise ValueError(
                f"Invalid address format: '{address[:20]}...'. Expected 0x-prefixed EVM address or Solana base58."
            )
        t0 = time.time()
        if not force_refresh:
            cached = self._get_cached(address, chain)
            if cached:
                return cached
        result = RIPResult(token_address=address, chain=chain, last_updated=datetime.now(UTC).isoformat())
        for sig_id, weight in self.weights.items():
            result.signals[sig_id] = SignalCategory(name=SIGNAL_NAMES.get(sig_id, sig_id), weight=weight)
        analyzers = [
            self._analyze_lp_health(result),
            self._analyze_deployer_risk(result),
            self._analyze_smart_money_flow(result),
            self._analyze_bundle_pattern(result),
            self._analyze_social_velocity(result),
            self._analyze_contract_risk(result),
            self._analyze_liquidity_migration(result),
        ]
        await asyncio.gather(*analyzers, return_exceptions=True)
        result.score = sum(sig.weighted_contribution() for sig in result.signals.values())
        for level, (lo, hi) in SCORE_THRESHOLDS.items():
            if lo <= result.score < hi:
                result.imminence = level
                break
        self._generate_warnings(result)
        self._generate_recommendations(result)
        result.scan_duration_ms = (time.time() - t0) * 1000
        self._set_cache(result)
        return result

    async def _analyze_lp_health(self, result: RIPResult) -> None:
        sig = result.signals["lp_health"]
        try:
            dex_data = None
            result.sources_used.append("dexscreener")
            try:
                dex_data = await self._fetch_dexscreener_pool(result.token_address, result.chain)
            except Exception:
                logger.debug("dexscreener_pool_fetch_failed %s", result.token_address[:12])
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
                    lock_info = await self._check_lp_lock(result.token_address, result.chain)
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
                except Exception:
                    logger.debug("lp_lock_check_failed %s", result.token_address[:12])
                    sig.score += 5
                    result.sources_failed.append("lp_lock_check")
            else:
                sig.score += 15
                sig.evidence.append("No DEX pool data found - token may have no real liquidity")
            sig.confidence = min(1.0, sig.score / 50) if sig.score > 0 else 0.3
        except Exception as e:
            logger.warning(f"LP health analysis failed for {result.token_address}: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.1
            result.sources_failed.append("lp_health_analysis")

    async def _analyze_deployer_risk(self, result: RIPResult) -> None:
        sig = result.signals["deployer_risk"]
        try:
            deployer = await self._resolve_deployer(result.token_address, result.chain)
            if not deployer:
                sig.score += 10
                sig.evidence.append("Could not resolve deployer address")
                sig.confidence = 0.3
                result.sources_failed.append("deployer_resolution")
                return
            result.sources_used.append("deployer_analysis")
            age_info = await self._check_wallet_age(deployer, result.chain)
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
            history = await self._check_deployer_history(deployer, result.chain)
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
        except Exception as e:
            logger.warning(f"Deployer risk analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.2
            result.sources_failed.append("deployer_risk_analysis")

    async def _analyze_smart_money_flow(self, result: RIPResult) -> None:
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
                        sig.evidence.append(
                            f"{len(insider_sells)} insider sell(s) detected (${total_insider:,.0f} total)"
                        )
                    if whale_sells:
                        sig.score += 10
                        sig.flags.append("whale_selling")
                        sig.evidence.append(f"{len(whale_sells)} whale(s) reducing position")
                    if deployer_moves:
                        sig.score += 25
                        sig.flags.append("deployer_cashing_out")
                        sig.evidence.append(f"Deployer sent funds to exchange ({len(deployer_moves)} txns)")
            except (ImportError, AttributeError):
                result.sources_failed.append("smart_money_tracker")
            try:
                holder_data = await self._fetch_holder_data(result.token_address, result.chain)
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
            except Exception:
                logger.debug("holder_data_fetch_failed %s", result.token_address[:12])
                result.sources_failed.append("holder_data")
            sig.confidence = min(0.8, 0.3 + (sig.score / 50))
        except Exception as e:
            logger.warning(f"Smart money flow analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.15
            result.sources_failed.append("smart_money_flow_analysis")

    async def _analyze_bundle_pattern(self, result: RIPResult) -> None:
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
        except Exception as e:
            logger.warning(f"Bundle analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.2
            result.sources_failed.append("bundle_pattern_analysis")

    async def _analyze_social_velocity(self, result: RIPResult) -> None:
        sig = result.signals["social_velocity"]
        try:
            try:
                from app.domains.scanners.social_velocity import SocialVelocityAnalyzer

                analyzer = SocialVelocityAnalyzer()
                velocity = await analyzer.analyze(result.token_address)
                result.sources_used.append("social_velocity_analyzer")
                if velocity:
                    mention_spike = velocity.get("mention_spike_pct", 0)  # type: ignore[attr-defined]
                    shill_score = velocity.get("shill_score", 0)  # type: ignore[attr-defined]
                    sentiment_change = velocity.get("sentiment_shift", 0)  # type: ignore[attr-defined]
                    if mention_spike > 500:
                        sig.score += 15
                        sig.flags.append("massive_mention_spike")
                        sig.evidence.append(
                            f"Social mentions spiked {mention_spike:.0f}% - suspicious coordinated hype"
                        )
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
            except (ImportError, AttributeError):
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
        except Exception as e:
            logger.warning(f"Social velocity analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.15
            result.sources_failed.append("social_velocity_analysis")

    async def _analyze_contract_risk(self, result: RIPResult) -> None:
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
        except Exception as e:
            logger.warning(f"Contract risk analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.2
            result.sources_failed.append("contract_risk_analysis")

    async def _analyze_liquidity_migration(self, result: RIPResult) -> None:
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
                        sig.evidence.append(
                            f"LP REMOVED! ${lp_status.get('amount_removed_usd', 0):,.0f} withdrawn in last 24h"
                        )
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
            if not result.signals["liquidity_migration"].evidence:
                try:
                    dex_data = await self._fetch_dexscreener_pool(result.token_address, result.chain)
                    if dex_data and dex_data.get("pairs"):
                        pair = dex_data["pairs"][0]
                        liq_change = (
                            pair.get("liquidity", {}).get("change_24h", 0)
                            if isinstance(pair.get("liquidity"), dict)
                            else 0
                        )
                        if liq_change < -50:
                            sig.score += 15
                            sig.flags.append("liquidity_drop")
                            sig.evidence.append(f"Liquidity dropped {abs(liq_change):.0f}% in 24h")
                except Exception:
                    logger.debug("liquidity change parse failed", exc_info=True)
            sig.confidence = min(0.7, 0.3 + (sig.score / 40))
        except Exception as e:
            logger.warning(f"Liquidity migration analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.15
            result.sources_failed.append("liquidity_migration_analysis")

    def _generate_warnings(self, result: RIPResult) -> None:
        flag_warnings = {
            "honeypot": "\u26a0\ufe0f HONEYPOT: Token cannot be sold - users are trapped",
            "unlocked_liquidity": "\u26a0\ufe0f LP UNLOCKED: Creator can pull all liquidity at any moment",
            "lp_removed": "\u26a0\ufe0f LP REMOVED: Liquidity has been withdrawn - imminent rug",
            "known_rug_deployer": "\u26a0\ufe0f KNOWN RUG DEPLOYER: This wallet has rugged before",
            "deployer_cashing_out": "\u26a0\ufe0f DEPLOYER CASHING OUT: Sending funds to exchanges",
            "insider_selling": "\u26a0\ufe0f INSIDER SELLING: Team/insiders are dumping",
            "bundled_launch": "\u26a0\ufe0f BUNDLED LAUNCH: Coordinated token launch",
            "coordinated_shilling": "\u26a0\ufe0f COORDINATED SHILLING: Artificial social hype",
            "active_ownership": "\u26a0\ufe0f ACTIVE OWNERSHIP: Owner can modify contract",
            "upgradable_proxy": "\u26a0\ufe0f UPGRADABLE PROXY: Contract can be changed",
            "new_deployer_wallet": "\u26a0\ufe0f NEW DEPLOYER: Disposable wallet, < 7 days old",
            "single_sided_lp": "\u26a0\ufe0f SINGLE-SIDED LP: Potential price manipulation",
            "lp_to_exchange": "\u26a0\ufe0f LP TO CEX: Liquidity tokens sent to exchange",
            "sell_pressure": "\u26a0\ufe0f SELL PRESSURE: More sells than buys",
            "massive_mention_spike": "\u26a0\ufe0f HYPE SPIKE: Social mentions up 500%+ - suspicious",
            "serial_deployer": "\u26a0\ufe0f SERIAL DEPLOYER: Created 5+ tokens",
            "concentrated_holdings": "\u26a0\ufe0f CONCENTRATED HOLDINGS: Top 10 own >80% of supply",
            "holder_exodus": "\u26a0\ufe0f HOLDER EXODUS: Large drop in holder count",
        }
        seen = set()
        for sig in result.signals.values():
            for flag in sig.flags:
                if flag in flag_warnings and flag not in seen:
                    result.warnings.append(flag_warnings[flag])
                    seen.add(flag)
        if not result.warnings:
            result.warnings.append(
                "\U0001f7e2 No imminent rug indicators detected"
                if result.score < 30
                else "\U0001f50d Some risk signals present - monitor closely"
            )

    def _generate_recommendations(self, result: RIPResult) -> None:
        flag_recos = {
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
        priority_flags = ["honeypot", "lp_removed", "unlocked_liquidity", "deployer_cashing_out"]
        for sig_name in ["lp_health", "contract_risk", "smart_money_flow"]:
            sig = result.signals.get(sig_name)
            if sig:
                for flag in priority_flags:
                    if flag in sig.flags:
                        result.recommendations.insert(0, f"\U0001f534 URGENT - {flag_recos.get(flag, 'Exit position')}")
        for sig in result.signals.values():
            for flag in sig.flags:
                if flag in flag_recos and flag_recos[flag] not in result.recommendations:
                    result.recommendations.append(f"\u2022 {flag_recos[flag]}")
        if not result.recommendations:
            if result.score < 30:
                result.recommendations.append("\U0001f7e2 No urgent action needed - low risk")
            elif result.score < 55:
                result.recommendations.append("\U0001f4ca Monitor token closely for the next 24-48 hours")
            else:
                result.recommendations.append("\u26a0\ufe0f Consider reducing exposure")

    async def _fetch_dexscreener_pool(self, address: str, chain: str) -> dict[str, Any] | None:
        import httpx

        chain_map = {
            "ethereum": "ethereum",
            "base": "base",
            "bsc": "bsc",
            "polygon": "polygon",
            "arbitrum": "arbitrum",
            "avalanche": "avalanche",
            "solana": "solana",
            "optimism": "optimism",
        }
        api_chain = chain_map.get(chain, chain)
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        if api_chain != "ethereum":
            url = f"https://api.dexscreener.com/token/v1/{api_chain}/{address}"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("pairs") and len(data["pairs"]) > 0:
                        return data
                    pairs = data.get("pairs", [])
                    if pairs:
                        return data
        except Exception:
            logger.debug("dexscreener fetch failed", exc_info=True)
        return None

    async def _resolve_deployer(self, address: str, chain: str) -> str | None:
        import httpx

        try:
            if chain == "solana":
                rpc = "https://api.mainnet-beta.solana.com"
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.post(
                        rpc,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getAccountInfo",
                            "params": [address, {"encoding": "base64"}],
                        },
                    )
                    if resp.status_code == 200:
                        return address[:16]
            else:
                rpc = "https://eth.llamarpc.com"
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.post(
                        rpc,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "eth_getTransactionReceipt",
                            "params": [address],
                        },
                    )
        except Exception:
            logger.debug("deployer resolve failed", exc_info=True)
        return None

    async def _check_wallet_age(self, wallet: str, chain: str) -> dict[str, Any] | None:
        import time

        import httpx

        try:
            if chain == "solana":
                rpc = "https://api.mainnet-beta.solana.com"
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.post(
                        rpc,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [wallet, {"limit": 1}],
                        },
                    )
                    if resp.status_code == 200:
                        sigs = resp.json().get("result", [])
                        if sigs:
                            block_time = sigs[0].get("blockTime")
                            if block_time:
                                return {
                                    "age_days": round((time.time() - block_time) / 86400, 1),
                                    "first_tx": sigs[0].get("signature", ""),
                                }
            return None
        except Exception:
            logger.debug("wallet_age_check_failed")
            return None

    async def _check_deployer_history(self, deployer: str, chain: str) -> dict[str, Any]:
        result = {"total_deployed": 0, "rug_tokens": 0, "active_tokens": 0}
        try:
            from app.scam_database import ScamDatabase

            db = ScamDatabase()
            scam_records = db.search_by_deployer(deployer)
            if scam_records:
                result["total_deployed"] = len(scam_records)
                result["rug_tokens"] = len([r for r in scam_records if r.get("type") == "rug"])
                result["active_tokens"] = len([r for r in scam_records if r.get("active", False)])
        except (ImportError, AttributeError):
            pass
        return result

    async def _check_lp_lock(self, address: str, chain: str) -> dict[str, Any]:
        result = {"locked": False, "unlocked": True, "unlock_date": None}
        try:
            from app.domains.scanners.liquidity_verifier import LiquidityVerifier

            verifier = LiquidityVerifier()
            lp_info = await verifier.check_lock(address, chain)  # type: ignore[attr-defined]
            if lp_info:
                result.update(lp_info)
        except (ImportError, AttributeError):
            pass
        return result

    async def _fetch_holder_data(self, address: str, chain: str) -> dict[str, Any] | None:
        """Fetch holder count and concentration data."""
        return None  # Placeholder - would call holder API


# ═══════════════════════════════════════════════════════════════

_instance: RugImminencePredictor | None = None


def get_predictor(weights: dict[str, float] | None = None) -> RugImminencePredictor:
    """Get or create the singleton predictor instance."""
    global _instance
    if _instance is None:
        _instance = RugImminencePredictor(weights)
    return _instance
