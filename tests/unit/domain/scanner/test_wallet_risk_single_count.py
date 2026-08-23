"""TOP-100 #20 regression: honeypot exposure must count once, not twice.

The wallet risk scorer used to add +25 (HOLDS_SCAM_TOKENS_n) AND +20
(HONEYPOT_HOLDER) for the same signal — one honeypot exposure cost +45.
The duplicate block was removed; the surviving flag carries the count.
"""

from __future__ import annotations

import pytest

from app.unified_scanner.models import WalletRiskFactors
from app.unified_scanner.scanner import _compute_wallet_risk


def _factors(**overrides: object) -> WalletRiskFactors:
    kwargs: dict[str, object] = {}
    kwargs.update(overrides)
    return WalletRiskFactors(**kwargs)  # type: ignore[arg-type]


class TestHoneypotSingleCount:
    def test_honeypot_holdings_cost_25_not_45(self) -> None:
        factors = _factors(honeypot_tokens_owned=2)
        flags = _compute_wallet_risk(factors, data_sources=["token_scan"])
        assert factors.total_risk_score == 25.0
        assert "HOLDS_SCAM_TOKENS_2" in flags
        assert "HONEYPOT_HOLDER" not in flags

    def test_clean_wallet_scores_zero(self) -> None:
        factors = _factors()
        flags = _compute_wallet_risk(factors, data_sources=[])
        assert factors.total_risk_score == 0.0
        assert flags == []

    @pytest.mark.parametrize(
        ("owned", "expected_flag"),
        [(1, "HOLDS_SCAM_TOKENS_1"), (5, "HOLDS_SCAM_TOKENS_5")],
    )
    def test_flag_carries_the_count(self, owned: int, expected_flag: str) -> None:
        factors = _factors(honeypot_tokens_owned=owned)
        flags = _compute_wallet_risk(factors, data_sources=["token_scan"])
        assert expected_flag in flags
