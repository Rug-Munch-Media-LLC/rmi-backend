"""
Portfolio Risk — Token Risk Scorer
Scores tokens using heuristics or the unified scanner when available.
"""
from __future__ import annotations
from typing import Any

from app.core.logging import get_logger
from app.domains.portfolio.portfolio_risk_package.constants import COMMON_TOKENS

logger = get_logger(__name__)

KNOWN_SCAM_TOKENS: set[str] = set()

class TokenRiskScorer:
    def __init__(self) -> None:
        self._scanner_available = False
        self._try_import_scanner()

    def _try_import_scanner(self) -> None:
        try:
            import importlib
            self._scanner_available = importlib.util.find_spec("app.unified_scanner") is not None
        except ImportError:
            pass

    async def score_token(self, chain: str, address: str, symbol: str) -> dict[str, Any]:
        if not address or address == "0x0000000000000000000000000000000000000000":
            return {"risk_score": 100, "risk_flags": [], "reason": "Native token"}
        if self._scanner_available:
            try:
                from app.unified_scanner import scan_token as _scan_token
                result = await _scan_token(chain, address)
                if result:
                    return {"risk_score": result.get("safety_score", 50), "risk_flags": result.get("risk_flags", []), "reason": "Scanner"}
            except (AttributeError, ImportError):
                pass
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
        scam_indicators = ["ELON", "MUSK", "SHIBA", "FLOCKI", "SQUID", "SAFEMOON"]
        for ind in scam_indicators:
            if ind in upper_sym:
                score -= 15
                flags.append(f"SCAM_INDICATOR_{ind}")
                break
        for chain_tokens in COMMON_TOKENS.values():
            for _known_sym, known_addr in chain_tokens.items():
                if known_addr.lower() == addr_lower:
                    return {"risk_score": 90, "risk_flags": [], "reason": "Known token"}
        return {"risk_score": max(0, min(100, score)), "risk_flags": flags, "reason": "Heuristic"}
