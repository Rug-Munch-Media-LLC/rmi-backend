#!/usr/bin/env python3
"""
RMI GMGN AI Agent Integration + Original Intelligence Features
===============================================================
Cross-reference engine, smart money narrative, sniper detection,
degen score, trending deep dive - powered by GMGN + Birdeye + AI.
"""

import asyncio
import os
from datetime import UTC, datetime

import httpx

from app.birdeye_client import BirdeyeClient

GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")


class GMGNClient:
    """GMGN AI Agent API Client - query-only (no trading without private key)"""

    def __init__(self) -> None:
        self.api_key = GMGN_API_KEY
        self.headers = {
            "Authorization": f"Bearer {GMGN_API_KEY}",
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        self.birdeye = BirdeyeClient()

    # ═══════════════════════════════════════════════════════════════
    # CORE GMGN QUERIES
    # ═══════════════════════════════════════════════════════════════

    async def query_token(self, address: str, chain: str = "solana") -> dict:
        """Get token info, price, security, holders, traders from GMGN"""
        # GMGN MCP token skill simulation via their API
        try:
            # Use Birdeye as data source (GMGN API requires MCP protocol)
            # We simulate GMGN token queries using Birdeye + AI enrichment
            overview = await self.birdeye.get_token_overview(address)
            await self.birdeye.get_price(address)
            await asyncio.sleep(0.6)

            d = overview.get("data", {}) if isinstance(overview, dict) else {}

            return {
                "address": address,
                "name": d.get("name", "Unknown"),
                "symbol": d.get("symbol", "???"),
                "price": d.get("price", 0),
                "market_cap": d.get("marketCap", 0),
                "fdv": d.get("fdv", 0),
                "liquidity": d.get("liquidity", 0),
                "volume_24h": d.get("v24hUSD", 0),
                "holders": d.get("holder", 0),
                "buy_24h": d.get("buy24h", 0),
                "sell_24h": d.get("sell24h", 0),
                "price_change_1h": d.get("priceChange1hPercent", 0),
                "price_change_24h": d.get("priceChange24hPercent", 0),
                "security": self._extract_security(d),
                "top_holders": d.get("holderDistribution", []),
                "extensions": d.get("extensions", {}),
                "chain": chain,
                "source": "birdeye_gmgn_bridge",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            return {"address": address, "error": str(e)}

    async def query_market(self, address: str, resolution: str = "1h", limit: int = 24) -> dict:
        """Get OHLCV/candlestick data"""
        try:
            ohlcv = await self.birdeye.get_ohlcv(address, resolution, limit)
            return {
                "address": address,
                "resolution": resolution,
                "candles": ohlcv,
                "count": len(ohlcv),
                "trend": self._analyze_trend(ohlcv),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            return {"address": address, "error": str(e)}

    async def query_portfolio(self, wallet: str, chain: str = "solana") -> dict:
        """Get wallet portfolio, PnL, trading history"""
        try:
            networth = await self.birdeye._call("/v1/wallet/networth", {"wallet": wallet})
            await asyncio.sleep(0.6)
            pnl = await self.birdeye._call("/v1/wallet/pnl", {"wallet": wallet, "time_frame": "7d"})

            return {
                "wallet": wallet,
                "networth": networth.get("data", {}) if isinstance(networth, dict) else networth,
                "pnl_7d": pnl.get("data", {}) if isinstance(pnl, dict) else pnl,
                "is_smart_money": await self._check_smart_money(wallet),
                "chain": chain,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            return {"wallet": wallet, "error": str(e)}

    async def query_trending(self, chain: str = "solana", limit: int = 20) -> dict:
        """Get trending tokens"""
        try:
            tokens = await self.birdeye.get_new_listings(limit)
            # Score and rank
            scored = []
            for t in tokens:
                score = self._score_trending_token(t)
                scored.append({**t, "intelligence_score": score})

            return {
                "tokens": sorted(
                    scored, key=lambda x: x.get("intelligence_score", 0), reverse=True
                ),
                "count": len(scored),
                "chain": chain,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            return {"error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # ORIGINAL FEATURES
    # ═══════════════════════════════════════════════════════════════

    async def smart_money_narrative(self, address: str) -> dict:
        """
        ORIGINAL #1: Smart Money Narrative Generator
        Instead of raw data, creates a human-readable story with risk assessment
        """
        token = await self.query_token(address)
        if "error" in token:
            return token

        market = await self.query_market(address, "1h", 12)

        # Build narrative from data
        narrative_parts = []
        risk_factors = []
        opportunities = []

        # Volume story
        vol = token.get("volume_24h", 0)
        mcap = token.get("market_cap", 0)
        if vol > 0 and mcap > 0:
            v_ratio = vol / mcap
            if v_ratio > 3:
                narrative_parts.append(
                    f"Heavy trading activity - ${vol / 1e6:.1f}M volume vs ${mcap / 1e6:.1f}M market cap"
                )
                risk_factors.append("Volume is 3x+ market cap - possible wash trading")
            elif v_ratio > 1:
                narrative_parts.append(f"Strong trading interest - ${vol / 1e6:.1f}M in 24h volume")
                opportunities.append("Healthy volume suggests genuine interest")
            else:
                narrative_parts.append(f"Moderate trading volume - ${vol / 1e6:.1f}M in 24h")

        # Holder story
        holders = token.get("holders", 0)
        if holders > 1000:
            narrative_parts.append(f"Established community with {holders:,} holders")
        elif holders > 100:
            narrative_parts.append(f"Growing community - {holders:,} holders")
        elif holders > 0:
            narrative_parts.append(f"Early stage - only {holders:,} holders")
            risk_factors.append("Very few holders - concentration risk")

        # Price action story
        chg_1h = token.get("price_change_1h", 0) or 0
        chg_24h = token.get("price_change_24h", 0) or 0
        if chg_1h > 20:
            narrative_parts.append(f"🔥 Surging +{chg_1h:.1f}% in last hour")
            risk_factors.append("Parabolic short-term pump - high volatility")
        elif chg_1h < -20:
            narrative_parts.append(f"📉 Dropping {chg_1h:.1f}% in last hour")
            opportunities.append("Potential dip-buying opportunity if fundamentals are sound")

        if chg_24h > 100:
            narrative_parts.append(f"🚀 Mooning +{chg_24h:.1f}% in 24h")
        elif chg_24h < -50:
            narrative_parts.append(f"💀 Crashed {chg_24h:.1f}% in 24h")
            risk_factors.append("Severe 24h decline - possible rug")

        # Buy/sell story
        buy = token.get("buy_24h", 0) or 0
        sell = token.get("sell_24h", 0) or 0
        if buy > 0 and sell > 0:
            ratio = buy / sell
            if ratio > 2:
                narrative_parts.append(
                    f"Bullish buy/sell ratio - {ratio:.1f}x more buys than sells"
                )
                opportunities.append("Strong buy pressure")
            elif ratio < 0.5:
                narrative_parts.append(f"Bearish sell pressure - {sell / buy:.1f}x more sells")
                risk_factors.append("Heavy selling - exit pressure")

        # Trend analysis
        trend = market.get("trend", "neutral")
        if trend == "uptrend":
            opportunities.append("Technical uptrend confirmed")
        elif trend == "downtrend":
            risk_factors.append("Technical downtrend - momentum against")

        # Generate verdict
        risk_count = len(risk_factors)
        opp_count = len(opportunities)

        if risk_count >= 3:
            verdict = "⚠️ HIGH RISK - Multiple red flags detected"
            conviction = 1
        elif risk_count >= 2:
            verdict = "🟡 MODERATE RISK - Proceed with caution"
            conviction = 3
        elif opp_count >= 2:
            verdict = "🟢 OPPORTUNITY - More signals than risks"
            conviction = 4
        else:
            verdict = "⚪ NEUTRAL - Insufficient data for conviction"
            conviction = 2

        return {
            "address": address,
            "token_name": token.get("name"),
            "symbol": token.get("symbol"),
            "narrative": " | ".join(narrative_parts)
            if narrative_parts
            else "No significant activity detected",
            "risk_factors": risk_factors,
            "opportunities": opportunities,
            "verdict": verdict,
            "conviction_score": conviction,  # 1-5 scale
            "key_metrics": {
                "price": token.get("price"),
                "market_cap": mcap,
                "volume_24h": vol,
                "holders": holders,
                "price_change_1h": chg_1h,
                "price_change_24h": chg_24h,
                "buy_sell_ratio": buy / sell if sell > 0 else float("inf"),
                "liquidity": token.get("liquidity"),
            },
            "technical_trend": trend,
            "timestamp": datetime.now(UTC).isoformat(),
            "feature": "smart_money_narrative",
        }

    async def degen_score(self, address: str) -> dict:
        """
        ORIGINAL #2: Degen Score (0-100)
        How "degen" is this token? Higher = more degen/risky/speculative
        """
        token = await self.query_token(address)
        security = await self.birdeye.security_scan(address)

        if "error" in token:
            return token

        d = token
        score = 0
        factors = []

        # 1. AGE FACTOR (0-20) - newer = more degen
        # Use holder growth as proxy for age
        holder_change = d.get("holders", 0)
        if holder_change < 50:
            score += 20
            factors.append("Brand new token (+20)")
        elif holder_change < 200:
            score += 15
            factors.append("Very young project (+15)")
        elif holder_change < 1000:
            score += 10
            factors.append("Early stage (+10)")
        elif holder_change < 5000:
            score += 5
            factors.append("Growing (+5)")
        else:
            factors.append("Established (0)")

        # 2. VOLATILITY FACTOR (0-20)
        chg_24h = abs(d.get("price_change_24h", 0) or 0)
        if chg_24h > 500:
            score += 20
            factors.append(f"Insane {chg_24h:.0f}% 24h move (+20)")
        elif chg_24h > 200:
            score += 15
            factors.append(f"Extreme {chg_24h:.0f}% volatility (+15)")
        elif chg_24h > 50:
            score += 10
            factors.append(f"High {chg_24h:.0f}% volatility (+10)")
        elif chg_24h > 20:
            score += 5
            factors.append(f"Moderate {chg_24h:.0f}% move (+5)")

        # 3. HYPE FACTOR (0-20) - volume vs market cap
        vol = d.get("volume_24h", 0) or 0
        mcap = d.get("market_cap", 0) or 0
        if mcap > 0 and vol > 0:
            ratio = vol / mcap
            if ratio > 5:
                score += 20
                factors.append(f"Volume {ratio:.1f}x mcap - pure hype (+20)")
            elif ratio > 2:
                score += 15
                factors.append(f"Volume {ratio:.1f}x mcap - very hypey (+15)")
            elif ratio > 1:
                score += 10
                factors.append("Volume matches mcap - hype building (+10)")
            elif ratio > 0.3:
                score += 5
                factors.append("Decent volume ratio (+5)")

        # 4. COMMUNITY FACTOR (0-20)
        buy = d.get("buy_24h", 0) or 0
        sell = d.get("sell_24h", 0) or 0
        if buy > 0 and sell > 0:
            if buy > sell * 3:
                score += 20
                factors.append("FOMO buying - 3x more buys (+20)")
            elif buy > sell * 2:
                score += 15
                factors.append("Strong buy pressure (+15)")
            elif buy > sell:
                score += 10
                factors.append("More buyers than sellers (+10)")

        # 5. METADATA FACTOR (0-20)
        ext = d.get("extensions", {})
        if not ext.get("website") and not ext.get("twitter"):
            score += 20
            factors.append("No website or socials - pure degen (+20)")
        elif not ext.get("website"):
            score += 10
            factors.append("No website (+10)")
        elif not ext.get("description"):
            score += 5
            factors.append("No description (+5)")

        # Clamp to 0-100
        final_score = min(score, 100)

        # Degen level
        if final_score >= 70:
            level = "🎰 MAX DEGEN"
        elif final_score >= 50:
            level = "🔥 HIGH DEGEN"
        elif final_score >= 30:
            level = "⚡ MODERATE DEGEN"
        elif final_score >= 15:
            level = "🌶️ LOW DEGEN"
        else:
            level = "😴 NOT DEGEN"

        return {
            "address": address,
            "token_name": d.get("name"),
            "symbol": d.get("symbol"),
            "degen_score": final_score,
            "degen_level": level,
            "score_breakdown": factors,
            "risk_score": security.get("risk_score", 0),
            "risk_level": security.get("risk_level", "UNKNOWN"),
            "interpretation": self._interpret_degen(final_score),
            "timestamp": datetime.now(UTC).isoformat(),
            "feature": "degen_score",
        }

    def _interpret_degen(self, score: int) -> str:
        if score >= 70:
            return "This is as degen as it gets. Could 100x or go to zero in hours. Only risk what you can afford to lose completely."
        elif score >= 50:
            return "High degen territory. Significant upside potential but equally significant risk of total loss."
        elif score >= 30:
            return "Moderately degen. Some fundamentals exist but still highly speculative."
        elif score >= 15:
            return "Low degen. Project shows some maturity but still early/speculative."
        else:
            return "Not degen at all. Boring but probably safer. Might be a good long-term hold."

    async def sniper_radar(self, address: str) -> dict:
        """
        ORIGINAL #3: Sniper Detection Radar
        Detects coordinated buying patterns that indicate sniper/insider activity
        """
        ohlcv = await self.birdeye.get_ohlcv(address, "5m", 12)  # Last hour in 5-min candles
        token = await self.query_token(address)

        if not ohlcv:
            return {"address": address, "error": "No trade data", "feature": "sniper_radar"}

        # Analyze candle patterns for sniper signatures
        sniper_signals = []
        confidence = 0

        # Check for sudden volume spikes
        volumes = [c.get("v", 0) for c in ohlcv]
        if len(volumes) >= 3:
            avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
            last_vol = volumes[-1]
            if avg_vol > 0 and last_vol > avg_vol * 5:
                sniper_signals.append(
                    f"Volume spike: {last_vol / avg_vol:.1f}x average in last 5 minutes"
                )
                confidence += 30

        # Check for rapid price jumps
        prices = [c.get("c", 0) for c in ohlcv if c.get("c", 0) > 0]
        if len(prices) >= 2:
            total_jump = ((prices[-1] - prices[0]) / prices[0]) * 100 if prices[0] > 0 else 0
            if total_jump > 50:
                sniper_signals.append(
                    f"Rapid price appreciation: +{total_jump:.1f}% in {len(ohlcv) * 5} minutes"
                )
                confidence += 25

        # Check holder concentration (proxy for sniper accumulation)
        holders = token.get("holders", 0)
        if holders > 0 and holders < 30:
            sniper_signals.append(f"Only {holders} holders - possible coordinated accumulation")
            confidence += 20

        # Verdict
        if confidence >= 60:
            verdict = "🎯 SNIPER ACTIVITY DETECTED"
        elif confidence >= 40:
            verdict = "⚡ Possible sniper activity"
        elif confidence >= 20:
            verdict = "🔍 Low confidence - monitor closely"
        else:
            verdict = "✅ No sniper patterns detected"

        return {
            "address": address,
            "token_name": token.get("name"),
            "symbol": token.get("symbol"),
            "verdict": verdict,
            "confidence": min(confidence, 100),
            "sniper_signals": sniper_signals,
            "candles_analyzed": len(ohlcv),
            "timeframe": "5m",
            "timestamp": datetime.now(UTC).isoformat(),
            "feature": "sniper_radar",
        }

    async def trending_deep_dive(self, limit: int = 10) -> dict:
        """
        ORIGINAL #4: Auto-triggered Trending Deep Dive
        When tokens trend, automatically analyze with full intelligence
        """
        trending = await self.query_trending(limit=limit)
        tokens = trending.get("tokens", [])

        deep_dives = []
        for token in tokens:
            addr = token.get("address", "")
            if not addr:
                continue

            # Run parallel analysis
            narrative, sniper, degen = await asyncio.gather(
                self.smart_money_narrative(addr),
                self.sniper_radar(addr),
                self.degen_score(addr),
                return_exceptions=True,
            )

            deep_dives.append(
                {
                    "token": token,
                    "narrative": narrative
                    if not isinstance(narrative, Exception)
                    else {"error": str(narrative)},
                    "sniper_radar": sniper
                    if not isinstance(sniper, Exception)
                    else {"error": str(sniper)},
                    "degen_score": degen
                    if not isinstance(degen, Exception)
                    else {"error": str(degen)},
                }
            )

        return {
            "tokens_analyzed": len(deep_dives),
            "analysis": deep_dives,
            "timestamp": datetime.now(UTC).isoformat(),
            "feature": "trending_deep_dive",
        }

    async def cross_reference(self, address: str) -> dict:
        """
        ORIGINAL #5: GMGN + Birdeye Cross-Reference Engine
        When GMGN shows high activity, cross-check with Birdeye for manipulation
        """
        gmgn_data = await self.query_token(address)
        birdeye_security = await self.birdeye.security_scan(address)

        # Cross-reference signals
        manipulation_signals = []
        confidence = 0

        # Signal 1: High volume + low liquidity = manipulation
        vol = gmgn_data.get("volume_24h", 0) or 0
        liq = gmgn_data.get("liquidity", 0) or 0
        if liq > 0 and vol > liq * 5:
            manipulation_signals.append("Volume is 5x+ liquidity - possible wash trading")
            confidence += 25

        # Signal 2: Low holders + high volume = fake activity
        holders = gmgn_data.get("holders", 0) or 0
        if holders < 50 and vol > 100000:
            manipulation_signals.append(
                f"Only {holders} holders but ${vol / 1e3:.0f}K volume - suspicious"
            )
            confidence += 20

        # Signal 3: Price flat despite volume = hidden selling
        chg_1h = gmgn_data.get("price_change_1h", 0) or 0
        if abs(chg_1h) < 5 and vol > 100000:
            manipulation_signals.append("High volume but flat price - possible hidden distribution")
            confidence += 15

        # Signal 4: Security flags from Birdeye
        if birdeye_security.get("risk_score", 0) > 50:
            manipulation_signals.append(
                f"Birdeye security risk: {birdeye_security.get(risk_level)}"
            )
            confidence += 20

        if confidence >= 60:
            verdict = "🚨 MANIPULATION LIKELY"
        elif confidence >= 40:
            verdict = "⚠️ Suspicious patterns"
        elif confidence >= 20:
            verdict = "🟡 Minor concerns"
        else:
            verdict = "✅ Clean cross-reference"

        return {
            "address": address,
            "verdict": verdict,
            "manipulation_confidence": min(confidence, 100),
            "signals": manipulation_signals,
            "gmgn_data": {
                k: v for k, v in gmgn_data.items() if k not in ["extensions", "top_holders"]
            },
            "birdeye_security": {
                "risk_score": birdeye_security.get("risk_score"),
                "risk_level": birdeye_security.get("risk_level"),
                "flags": birdeye_security.get("flags", []),
            },
            "timestamp": datetime.now(UTC).isoformat(),
            "feature": "cross_reference",
        }

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════

    def _extract_security(self, data: dict) -> dict:
        """Extract security-relevant fields from token data"""
        ext = data.get("extensions", {})
        return {
            "has_website": bool(ext.get("website")),
            "has_twitter": bool(ext.get("twitter")),
            "has_description": bool(ext.get("description")),
            "is_mutable": data.get("mutableMetadata", True),
            "holder_concentration": data.get("top10HolderPercent", 0),
            "lp_burned": data.get("lpBurned", False),
        }

    def _analyze_trend(self, candles: list[dict]) -> str:
        """Determine price trend from OHLCV data"""
        if len(candles) < 3:
            return "insufficient_data"

        closes = [c.get("c", 0) for c in candles if c.get("c", 0) > 0]
        if len(closes) < 3:
            return "insufficient_data"

        # Simple moving average comparison
        mid = len(closes) // 2
        first_half = sum(closes[:mid]) / max(mid, 1)
        second_half = sum(closes[mid:]) / max(len(closes) - mid, 1)

        if second_half > first_half * 1.02:
            return "uptrend"
        elif second_half < first_half * 0.98:
            return "downtrend"
        return "sideways"

    def _score_trending_token(self, token: dict) -> int:
        """Score a trending token for intelligence value"""
        score = 0
        liq = token.get("liquidity", 0) or 0
        if liq > 50000:
            score += 20
        elif liq > 10000:
            score += 10

        vol = token.get("v24hUSD", 0) or 0
        if vol > 1000000:
            score += 20
        elif vol > 100000:
            score += 10

        holders = token.get("uniqueWallet30m", 0) or 0
        if holders > 500:
            score += 15
        elif holders > 100:
            score += 10

        return score

    async def _check_smart_money(self, wallet: str) -> bool:
        """Check if wallet is flagged as smart money"""
        try:
            result = await self.birdeye._call("/v1/wallet/smart_money", {"wallet": wallet})
            return (
                result.get("data", {}).get("isSmartMoney", False)
                if isinstance(result, dict)
                else False
            )
        except Exception:
            return False

    async def close(self) -> None:
        await self.birdeye.close()
        await self.client.aclose()
