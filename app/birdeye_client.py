from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import httpx

from app.core.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerOpenError

BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BASE_URL = "https://public-api.birdeye.so"
HEADERS = {"X-API-KEY": BIRDEYE_API_KEY, "accept": "application/json"}

# Circuit breaker for Birdeye API
_birdeye_cb = AsyncCircuitBreaker(
    name="birdeye",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)


class BirdeyeClient:
    def __init__(self) -> None:
        self.headers = HEADERS
        self.client = httpx.AsyncClient(timeout=30.0)
        self.last_call = 0

    async def _call(self, endpoint: str, params: dict | None = None) -> dict:
        import time

        # Circuit breaker check
        if not await _birdeye_cb.can_call():
            stale = _birdeye_cb.get_stale_result()
            if stale is not None:
                return stale
            raise CircuitBreakerOpenError(
                _birdeye_cb.name,
                _birdeye_cb.state,
                "Birdeye circuit OPEN — failing fast",
            )

        now = time.time()
        wait = 0.6 - (now - self.last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self.last_call = time.time()
        try:
            r = await self.client.get(f"{BASE_URL}{endpoint}", headers=self.headers, params=params or {})
            if r.status_code == 200:
                data = r.json()
                _birdeye_cb.set_stale_result(data)
                await _birdeye_cb.record_success()
                return data
            elif r.status_code >= 500:
                await _birdeye_cb.record_failure(is_server_error=True)
                return {"error": f"HTTP {r.status_code}"}
            else:
                await _birdeye_cb.record_failure(is_server_error=False)
                return {"error": f"HTTP {r.status_code}"}
        except Exception as e:
            await _birdeye_cb.record_failure(is_server_error=True)
            return {"error": str(e)}

    async def get_price(self, address: str) -> dict:
        return await self._call("/defi/price", {"address": address})

    async def get_token_overview(self, address: str) -> dict:
        return await self._call("/defi/token_overview", {"address": address})

    async def get_new_listings(self, limit: int = 20) -> list:
        r = await self._call("/defi/v2/tokens/new_listing", {"limit": limit, "offset": 0})
        return r.get("data", {}).get("items", []) if isinstance(r, dict) else []

    async def security_scan(self, address: str) -> dict:
        """Derived security analysis using ALL Birdeye market data"""
        overview = await self.get_token_overview(address)
        await asyncio.sleep(0.6)

        d = overview.get("data", {}) if isinstance(overview, dict) else {}

        if not d:
            return {"address": address, "error": "No data", "risk_score": -1}

        score = 0
        flags = []
        signals = []

        # 1. LIQUIDITY HEALTH (0-25 pts)
        mcap = d.get("marketCap", 0) or 0
        liq = d.get("liquidity", 0) or 0
        if mcap > 0 and liq > 0:
            ratio = liq / mcap
            if ratio < 0.05:
                score += 25
                flags.append("CRITICAL: Liquidity/MCap < 5% - easy manipulation")
            elif ratio < 0.15:
                score += 15
                flags.append("WARNING: Low liquidity ratio")
            elif ratio > 0.5:
                signals.append("Strong liquidity backing")
            else:
                signals.append("Normal liquidity levels")

        # 2. PRICE VOLATILITY (0-20 pts)
        changes = [abs(d.get(f"priceChange{t}Percent") or 0) for t in ["1m", "5m", "30m"]]
        avg_chg = sum(changes) / max(len(changes), 1)
        if avg_chg > 20:
            score += 20
            flags.append("EXTREME volatility - pump/dump in progress")
        elif avg_chg > 5:
            score += 10
            flags.append("High volatility - watch for manipulation")
        elif avg_chg < 1:
            signals.append("Stable price action")

        # 3. HOLDER HEALTH (0-20 pts)
        holders = d.get("holder", 0) or 0
        if holders < 20:
            score += 20
            flags.append(f"Very few holders ({holders}) - high concentration")
        elif holders < 100:
            score += 10
            flags.append(f"Low holder count ({holders})")
        elif holders > 500:
            signals.append(f"Healthy holder base ({holders:,}) wallets")

        # Check wallet change for suspicious activity
        uw_change = d.get("uniqueWallet30mChangePercent", 0) or 0
        if uw_change > 50:
            flags.append(f"Suspicious +{uw_change:.0f}% wallet growth in 30m - possible bots")
        elif uw_change > 20:
            flags.append(f"Rapid wallet growth +{uw_change:.0f}%")

        # 4. TRADE ACTIVITY (0-15 pts)
        last_trade = d.get("lastTradeUnixTime", 0) or 0
        if last_trade > 0:
            mins = (datetime.now(UTC).timestamp() - last_trade) / 60
            if mins > 60:
                score += 15
                flags.append(f"No trades for {int(mins)} min - possible dead token")
            elif mins > 30:
                score += 5
                flags.append(f"Low activity - last trade {int(mins)} min ago")
            else:
                signals.append("Active trading")

        # 5. METADATA QUALITY (0-10 pts)
        ext = d.get("extensions", {})
        has_web = bool(ext.get("website"))
        has_social = bool(ext.get("twitter") or ext.get("discord"))
        has_desc = bool(ext.get("description"))
        if not has_web and not has_social:
            score += 10
            flags.append("No website or socials - anonymous project")
        elif not has_web:
            score += 5
            flags.append("No website - transparency concern")
        elif has_desc:
            signals.append("Complete metadata - transparent project")

        # 6. VOLUME/MARKET CAP RATIO (0-10 pts) - wash trading detection
        v24h = d.get("v24hUSD", 0) or 0
        if mcap > 0 and v24h > 0:
            v_ratio = v24h / mcap
            if v_ratio > 5:
                score += 10
                flags.append(f"Volume {v_ratio:.1f}x MarketCap - WASH TRADING likely")
            elif v_ratio > 2:
                score += 5
                flags.append(f"Volume {v_ratio:.1f}x MarketCap - possible wash trading")
            elif v_ratio > 0.1:
                signals.append("Healthy volume/market cap ratio")

        # 7. BUY/SELL RATIO ANALYSIS (bonus signal)
        buy24h = d.get("buy24h", 0) or 0
        sell24h = d.get("sell24h", 0) or 0
        if buy24h > 0 and sell24h > 0:
            if sell24h > buy24h * 2:
                flags.append("Heavy sell pressure - 2x more sells than buys")
            elif buy24h > sell24h * 1.5:
                signals.append("Buy pressure dominant - bullish signal")

        # VERDICT
        if score >= 60:
            verdict = "HIGH RISK"
        elif score >= 35:
            verdict = "MEDIUM RISK"
        elif score >= 15:
            verdict = "LOW-MEDIUM RISK"
        else:
            verdict = "LOW RISK"

        return {
            "address": address,
            "token_name": d.get("name", "Unknown"),
            "symbol": d.get("symbol", "???"),
            "risk_score": min(score, 100),
            "risk_level": verdict,
            "price": d.get("price", 0),
            "market_cap": mcap,
            "liquidity": liq,
            "fdv": d.get("fdv", 0),
            "holders": holders,
            "number_markets": d.get("numberMarkets", 0),
            "volume_24h": v24h,
            "buy_24h": buy24h,
            "sell_24h": sell24h,
            "price_change_24h": d.get("priceChange24hPercent", 0),
            "wallet_growth_30m": uw_change,
            "last_trade": d.get("lastTradeHumanTime", ""),
            "flags": flags,
            "positive_signals": signals,
            "metadata": {"website": has_web, "socials": has_social, "description": has_desc},
            "analyzed_at": datetime.now(UTC).isoformat(),
            "birdeye_powered": True,
        }

    async def new_token_radar(self, limit: int = 20, min_liquidity: float = 1000) -> dict:
        tokens = await self.get_new_listings(limit)
        scored = []
        for t in tokens:
            liq = t.get("liquidity", 0) or 0
            if liq < min_liquidity:
                continue
            score = 0
            reasons = []
            if liq > 10000:
                score += 25
                reasons.append("Good liquidity")
            uw = t.get("uniqueWallet30m", 0) or 0
            if uw > 50:
                score += min(uw * 0.2, 20)
                reasons.append(f"{uw} recent wallets")
            score += min(t.get("trade24h", 0) or 0 * 0.01, 10)
            scored.append({**t, "opportunity_score": min(score, 50), "score_reasons": reasons})
        return {
            "tokens": sorted(scored, key=lambda x: x.get("opportunity_score", 0), reverse=True),
            "count": len(scored),
        }

    # ── Wallet Intelligence ──────────────────────────────────────────────────

    async def get_wallet_networth(self, wallet: str) -> dict:
        """Get wallet net worth in USD and token breakdown."""
        return await self._call("/v1/wallet/networth", {"wallet": wallet})

    async def get_wallet_pnl(self, wallet: str, timeframe: str = "7d") -> dict:
        """Get wallet profit/loss for a given timeframe."""
        return await self._call("/v1/wallet/pnl", {"wallet": wallet, "time_frame": timeframe})

    async def get_wallet_smart_money_status(self, wallet: str) -> dict:
        """Check if wallet is tagged as smart money."""
        return await self._call("/v1/wallet/smart_money", {"wallet": wallet})

    async def close(self) -> None:
        await self.client.aclose()
