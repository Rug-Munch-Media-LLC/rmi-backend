"""Happy-path tests for the /investigate user journey.

Covers ``app/domains/telegram/rugmunchbot/handlers/investigate._determine_verdict``
— the pure verdict function that turns a scan result + wallet forensics into
a (verdict, description) pair. Pure unit tests: no network, DB, or Telegram.

  * a high-risk profile → CRITICAL
  * a low-risk profile → SAFE
  * escalation signals (bundle / fresh wallets / low liquidity) push the
    verdict up
  * a honeypot is ALWAYS critical regardless of the numeric risk score
"""

from __future__ import annotations

import pytest

from app.domains.telegram.rugmunchbot.handlers.investigate import _determine_verdict

_VERDICTS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "SAFE")


def _scan(**overrides: object) -> dict:
    """A baseline low-risk scan result dict."""
    base: dict = {
        "risk_score": 10,
        "threats": [],
        "bundle_detected": False,
        "fresh_wallet_pct": 0,
        "honeypot": False,
        "liquidity": 1_000_000,
        "mcap": 5_000_000,
    }
    base.update(overrides)
    return base


def _wallet(**overrides: object) -> dict:
    base: dict = {"flags": []}
    base.update(overrides)
    return base


# ── Verdict output shape ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("scan", "wallet"),
    [
        (_scan(), _wallet()),
        (_scan(risk_score=90, threats=["Honeypot", "Owner not renounced"]), _wallet()),
    ],
)
def test_determine_verdict_returns_sane_tuple(scan: dict, wallet: dict) -> None:
    verdict, description = _determine_verdict(scan, wallet)
    assert verdict in _VERDICTS
    assert isinstance(description, str)
    assert description


# ── High-risk profile ─────────────────────────────────────────────────────


def test_determine_verdict_high_risk_profile_is_critical() -> None:
    scan = _scan(
        risk_score=90,
        threats=["Honeypot simulation failed", "LP unlocked", "Deployer rugged 3 tokens"],
    )
    verdict, description = _determine_verdict(scan, _wallet(flags=["fresh wallet", "funded by mixer"]))
    assert verdict == "CRITICAL"
    assert "3 threats" in description
    assert "wallet flags" in description


def test_determine_verdict_escalation_signals_raise_medium_to_high() -> None:
    scan = _scan(
        risk_score=45,
        bundle_detected=True,
        fresh_wallet_pct=60,
    )
    verdict, _ = _determine_verdict(scan, _wallet())
    # 45 + 15 (bundle) + 10 (fresh) = 70 → HIGH.
    assert verdict == "HIGH"


def test_determine_verdict_low_liquidity_high_mcap_adds_risk() -> None:
    scan = _scan(risk_score=35, liquidity=500, mcap=5_000_000)
    verdict, _ = _determine_verdict(scan, _wallet())
    # 35 + 10 (low liquidity vs mcap) = 45 → MEDIUM.
    assert verdict == "MEDIUM"


def test_determine_verdict_honeypot_always_critical() -> None:
    scan = _scan(risk_score=5, honeypot=True)
    verdict, description = _determine_verdict(scan, _wallet())
    assert verdict == "CRITICAL"
    assert "DANGER" in description


# ── Low-risk profile ──────────────────────────────────────────────────────


def test_determine_verdict_low_risk_profile_is_safe() -> None:
    scan = _scan(risk_score=5, threats=[])
    verdict, description = _determine_verdict(scan, _wallet())
    assert verdict == "SAFE"
    assert "no significant risks" in description.lower()


def test_determine_verdict_low_risk_with_minor_wallet_flags_is_low() -> None:
    scan = _scan(risk_score=25)
    verdict, _ = _determine_verdict(scan, _wallet(flags=["one minor flag"]))
    # 25 + 0 (single flag ≤ 2) = 25 → LOW.
    assert verdict == "LOW"
