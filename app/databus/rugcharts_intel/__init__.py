"""
RugCharts Intelligence Layer — Split into focused modules.
"""

from app.core.logging import get_logger

logger = get_logger("rugcharts_intel")

# Config & cache helpers
from .cache import _r, CACHE_TTL, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, HELIUS_KEY, ARKHAM_KEY, MORALIS_KEY

# Feature modules
from .smart_money import smart_money_feed
from .whale_alert import whale_alert_stream
from .token_launch import token_launch_scanner
from .insider import insider_pattern_detector
from .liquidity import liquidity_risk_monitor
from .holders import holder_health_score
from .cross_chain import cross_chain_entity
from .rug_patterns import rug_pattern_matcher, RUG_PATTERNS
from .developer import developer_reputation
from .token_report import instant_token_report, _extract_security_highlights

__all__ = [
    "smart_money_feed",
    "whale_alert_stream",
    "token_launch_scanner",
    "insider_pattern_detector",
    "liquidity_risk_monitor",
    "holder_health_score",
    "cross_chain_entity",
    "rug_pattern_matcher",
    "RUG_PATTERNS",
    "developer_reputation",
    "instant_token_report",
    "_extract_security_highlights",
    "_r",
    "CACHE_TTL",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_PASSWORD",
    "HELIUS_KEY",
    "ARKHAM_KEY",
    "MORALIS_KEY",
]
