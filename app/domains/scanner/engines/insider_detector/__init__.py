"""
Insider Detection Engine — Split into focused modules.
Re-exports all public API for backward compatibility.
"""

from app.core.logging import get_logger

from .models import InsiderType, Confidence, InsiderEvidence, InsiderReport
from .config import (
    DEXSCREENER_PROFILES, DEXSCREENER_BOOSTS, DEXSCREENER_TOKEN, DEXSCREENER_SEARCH,
    AVG_PRE_TRENDING_PUMP, AVG_CEX_LISTING_PUMP, AVG_KOL_PUMP, AVG_PUMPFUN_GRADUATION_PUMP,
    INSIDER_WINDOW_MIN, MIN_INSIDER_BUY_USD,
)
from .collectors import _collect_token_meta, _get_token_traders, _gather_events
from .helpers import _parse_timestamp, _ts_human
from .detectors import (
    _detect_pre_trending, _detect_pre_kol, _detect_dev_insider,
    _detect_cex_frontrun, _detect_pumpfun_graduation, _detect_snipers,
)
from .utils import (
    _deduplicate_evidence, _generate_insights, _check_early_buy,
)
from .scoring import detect_insiders, get_insider_score, find_pre_pump_wallets

logger = get_logger(__name__)

logger.info(
    "Insider Detection Engine loaded: %d detection methods, confidence scoring, PnL estimation", 6
)

__all__ = [
    "detect_insiders",
    "get_insider_score",
    "find_pre_pump_wallets",
    "InsiderReport",
    "InsiderEvidence",
    "InsiderType",
    "Confidence",
]
