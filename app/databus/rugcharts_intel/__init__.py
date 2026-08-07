"""
RugCharts Intelligence Layer — Split into focused modules.
"""

from app.core.logging import get_logger

logger = get_logger("rugcharts_intel")

# Config & cache helpers
from .cache import (  # noqa: E402 — imported after logger init; kept below module header for readability
    ARKHAM_KEY,
    CACHE_TTL,
    HELIUS_KEY,
    MORALIS_KEY,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
    _r,
)
from .cross_chain import (
    cross_chain_entity,
)
from .developer import (
    developer_reputation,
)
from .holders import (
    holder_health_score,
)
from .insider import insider_pattern_detector  # noqa: E402 — module docstring precedes import block
from .liquidity import liquidity_risk_monitor  # noqa: E402 — module docstring precedes import block
from .rug_patterns import RUG_PATTERNS, rug_pattern_matcher  # noqa: E402 — module docstring precedes import block

# Feature modules
from .smart_money import smart_money_feed  # noqa: E402 — module docstring precedes import block
from .token_launch import token_launch_scanner  # noqa: E402 — module docstring precedes import block
from .token_report import (  # noqa: E402 — module docstring precedes import block
    _extract_security_highlights,
    instant_token_report,
)
from .whale_alert import whale_alert_stream  # noqa: E402 — module docstring precedes import block

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
