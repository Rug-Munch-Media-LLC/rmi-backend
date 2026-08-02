"""Scanner service package — re-exports for backward compatibility."""

from app.domains.scanner.service.checks import (
    _check_cross_chain_deployer,
    _rag_scam_check,
)
from app.domains.scanner.service.external_checks import (
    _check_coingecko_trending,
    _check_fear_greed,
    _check_sourcify,
)
from app.domains.scanner.service.scan import quick_scan_text, scan_token
from app.domains.scanner.service.scoring import _calculate_safety_score
from app.domains.scanner.service.volume_analysis import _check_volume_anomaly

__all__ = [
    "_calculate_safety_score",
    "_check_coingecko_trending",
    "_check_cross_chain_deployer",
    "_check_fear_greed",
    "_check_sourcify",
    "_check_volume_anomaly",
    "_rag_scam_check",
    "quick_scan_text",
    "scan_token",
]
