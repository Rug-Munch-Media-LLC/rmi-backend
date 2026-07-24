"""Token risk scoring and pricing."""
from __future__ import annotations
import importlib
from typing import Any
from app.core.logging import get_logger
from .config import KNOWN_SCAM_TOKENS, COMMON_TOKENS, CHAIN_CONFIGS

logger = get_logger(__name__)

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

class TokenRiskScorer:
    def __init__(self) -> None:
        self._scanner_available = False
        self._import_error: str | None = None
        self._try_import_scanner()

    def _try_import_scanner(self) -> None:
        try:
            self._scanner_available = importlib.util.find_spec("app.unified_scanner") is not None
        except ImportError:
            pass

    async def score_token(self, chain: str, address: str, symbol: str) -> dict[str, Any]:
        if not address or address == "0x0000000000000000000000000000000000000000":
            return {"risk_score": 100, "risk_flags": [], "reason": "Native token (zero address)"}
        if self._scanner_available:
            try:
                from app.unified_scanner import scan_token as _scan_token
                result = await _scan_token(chain, address)
                if result:
                    return {"risk_score": result.get("safety_score", 50), "risk_flags": result.get("risk_flags", []), "reason": "Scanner"}
            except Exception:
                logger.debug("scanner_fetch_failed %s %s", chain, address[:12])
        return self._heuristic_score(chain, address, symbol)

    def _heuristic_score(self, chain: str, address: str, symbol: str) -> dict[str, Any]:
        score = 50
        flags = []
        addr_lower = address.lower()
        if addr_lower in KNOWN_SCAM_TOKENS:
            score -= 40
            flags.append("KNOWN_SCAM_TOKEN")
        if not addr_lower.startswith("0x") or len(address) < 20:
            score -= 20
            flags.append("NON_STANDARD_ADDRESS")
        upper_sym = symbol.upper() if symbol else ""
        scam_inds = ["ELON", "MUSK", "SHIBA", "FLOCKI", "SQUID", "SAFEMOON"]
        for ind in scam_inds:
            if ind in upper_sym:
                score -= 15
                flags.append(f"SCAM_INDICATOR_{ind}")
                break
        for chain_tokens in COMMON_TOKENS.values():
            for _known_sym, known_addr in chain_tokens.items():
                if known_addr.lower() == addr_lower:
                    return {"risk_score": 90, "risk_flags": [], "reason": "Known token"}
        return {"risk_score": max(0, min(100, score)), "risk_flags": flags, "reason": "Heuristic"}

class PriceOracle:
    def __init__(self) -> None:
        self._cache: dict[str, float] = {
            "ETH": 2800.0, "BTC": 68000.0, "SOL": 145.0, "BNB": 580.0, "MATIC": 0.55,
            "AVAX": 32.0, "MNT": 0.85, "FTM": 0.68, "USDC": 1.0, "USDT": 1.0,
            "DAI": 1.0, "WBTC": 68000.0, "LINK": 14.0, "UNI": 7.5, "PEPE": 0.000012,
            "SHIB": 0.000025, "AERO": 0.85, "CAKE": 2.10,
        }
        self._cache_time: float = 0
        self._cache_ttl = 300

    async def get_price(self, symbol: str) -> float:
        upper = symbol.upper()
        cached = self._cache.get(upper)
        if cached is not None:
            return cached
        try:
            if HTTPX_AVAILABLE:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"https://api.coingecko.com/api/v3/simple/price?ids={upper.lower()}&vs_currencies=usd")
                    if resp.status_code == 200:
                        data = resp.json()
                        price = data.get(upper.lower(), {}).get("usd")
                        if price:
                            self._cache[upper] = float(price)
                            return float(price)
        except Exception:
            logger.debug("coingecko_price_fetch_failed %s", symbol)
        return 0.0

    def estimate_native_price(self, chain: str) -> float:
        symbol = CHAIN_CONFIGS[chain]["native_symbol"]
        return self._cache.get(symbol.upper(), 0.0)
