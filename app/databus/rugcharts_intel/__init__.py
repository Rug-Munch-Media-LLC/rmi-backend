"""
RugCharts Intelligence Layer — Split into focused modules.
"""

from app.core.logging import get_logger

logger = get_logger("rugcharts_intel")

# Config & cache helpers
from .cache import ARKHAM_KEY, CACHE_TTL, HELIUS_KEY, MORALIS_KEY, REDIS_HOST, REDIS_PASSWORD, REDIS_PORT, _r
from .cross_chain import cross_chain_entity
from .developer import developer_reputation
from .holders import holder_health_score
from .insider import insider_pattern_detector
from .liquidity import liquidity_risk_monitor
from .rug_patterns import RUG_PATTERNS, rug_pattern_matcher

# Feature modules
from .smart_money import smart_money_feed
from .token_launch import token_launch_scanner
from .token_report import _extract_security_highlights, instant_token_report
from .whale_alert import whale_alert_stream

__all__ = [
    "ARKHAM_KEY",
    "CACHE_TTL",
    "HELIUS_KEY",
    "MORALIS_KEY",
    "REDIS_HOST",
    "REDIS_PASSWORD",
    "REDIS_PORT",
    "RUG_PATTERNS",
    "_extract_security_highlights",
    "_r",
    "cross_chain_entity",
    "developer_reputation",
    "holder_health_score",
    "insider_pattern_detector",
    "instant_token_report",
    "liquidity_risk_monitor",
    "rug_pattern_matcher",
    "smart_money_feed",
    "token_launch_scanner",
    "whale_alert_stream",
]
