"""Tests for consolidated scan enrichment (app/domains/scanner/scan_enrich.py).

Pure unit tests — every enrichment module entrypoint is mocked; no network,
no DB. Covers:
  * enrich_scan attaches a ``risk_signals`` dict with module flags + the
    pre-rendered signal strings + a consolidated ``rug_risk_score``
  * a failing module degrades to ``None`` fields (fail-open, never raises)
  * the /scan formatter emits the 🔍 Risk Signals section when signals fired
  * the formatter omits the section when the scan is clean
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from app.domains.scanner.scan_enrich import enrich_scan
from app.domains.telegram.rugmunchbot.formatters._sentinel import format_scan_report

TOKEN = "0x1234567890abcdef1234567890abcdef12345678"

# Module payloads mirror the real return shapes of each enrichment module.
BUNDLE_PAYLOAD = {
    "bundled": True,
    "bundle_pct": 62.0,
    "sniper_count": 8,
    "evidence": "heuristic: 8/13 holders bought in the same block",
}
REPUTATION_PAYLOAD = {
    "score": 40.0,
    "prior_deployments": 12,
    "repeat_deployer": True,
    "rug_history": False,
    "funding_source": None,
    "evidence": "deployer launched 12 tokens",
}
WASH_PAYLOAD = {
    "wash_trading": True,
    "wash_volume_pct": 40.0,
    "self_trade_count": 3,
    "evidence": "3 wallet(s) round-tripped BUY/SELL within 300s; 24h volume $12,000",
}
WHALE_PAYLOAD = {
    "net_flow": -5000.0,
    "whale_tx_count": 4,
    "smart_money_bought": False,
    "smart_money_sold": True,
    "evidence": "smart money net -5000 (7d)",
}
AUTHORITY_PAYLOAD = {
    "mintable": True,
    "freeze_authority": False,
    "lp_locked": True,
    "lp_lock_pct": 95.0,
    "renounced": False,
    "evidence": "authority=goplus;lp=own_lp_lock",
}
CROSSREF_PAYLOAD = {
    "known_scammer": True,
    "scammer_handle": "scammer1",
    "scammer_category": "rug_pull",
    "kol_handle": None,
    "kol_name": None,
}


def _scan_result() -> dict[str, Any]:
    """Minimal scan-result dict in the legacy formatter shape."""
    return {
        "address": TOKEN,
        "chain": "ethereum",
        "name": "Test Token",
        "symbol": "TST",
        "risk_score": 60,
        "created_at": None,
        "honeypot": False,
        "liquidity": 50_000,
        "top_holders": [],
        "socials": {},
        "bundle_detected": False,
        "errors": [],
    }


def _patch_all_modules(**overrides: Any):
    """Patch every enrichment entrypoint with an AsyncMock returning its payload."""
    patchers = {
        "bundle": patch(
            "app.domains.scanner.scan_enrich.detect_bundle",
            new=AsyncMock(return_value=BUNDLE_PAYLOAD),
        ),
        "reputation": patch(
            "app.domains.scanner.scan_enrich.deployer_reputation",
            new=AsyncMock(return_value=REPUTATION_PAYLOAD),
        ),
        "wash": patch(
            "app.domains.scanner.scan_enrich.detect_wash_trading",
            new=AsyncMock(return_value=WASH_PAYLOAD),
        ),
        "whale": patch(
            "app.domains.scanner.scan_enrich.whale_flow",
            new=AsyncMock(return_value=WHALE_PAYLOAD),
        ),
        "authority": patch(
            "app.domains.scanner.scan_enrich.check_authority",
            new=AsyncMock(return_value=AUTHORITY_PAYLOAD),
        ),
        "crossref": patch(
            "app.domains.scanner.scan_enrich.crossref_wallet",
            new=AsyncMock(return_value=CROSSREF_PAYLOAD),
        ),
    }
    for name, override in overrides.items():
        patchers[name] = override
    return patchers


async def test_enrich_attaches_risk_signals_with_consolidated_score():
    """All modules succeed: flags, signal strings and rug_risk_score attach."""
    patchers = _patch_all_modules()
    for p in patchers.values():
        p.start()
    try:
        result = await enrich_scan(_scan_result(), TOKEN, "ethereum")
    finally:
        for p in patchers.values():
            p.stop()

    rs = result["risk_signals"]

    # Individual module flags
    assert rs["bundled"] is True
    assert rs["deployer_repeat"] is True
    assert rs["wash_trading"] is True
    assert rs["whale_flow"] is True
    assert rs["mintable"] is True
    assert rs["freezable"] is False
    assert rs["lp_locked"] is True
    assert rs["known_scammer"] is True
    assert rs["kol_handle"] is None

    # Pre-rendered signal strings (pure module renderers)
    assert rs["bundle_signal"] == "bundled: 62% snipers"
    assert rs["reputation_signal"] == "deployer launched 12 tokens"
    assert rs["wash_signal"] == "wash trading: 40% self-trades"
    assert rs["whale_signal"] == "whales net selling"
    assert rs["authority_signal"] == "mintable"
    assert rs["crossref_signal"] == "deployer is known scammer @scammer1 (rug_pull)"

    # Consolidated rug risk score — known scammer + mintable + bundle + ...
    # = 40+35+25+22+20+15+12 (smart money sold) = 169 → capped 100,
    # critical (hard floor ≥ 70).
    score = rs["rug_risk_score"]
    assert score["score"] == 100
    assert score["verdict"] == "critical"
    assert "known_scammer" in score["flags"]
    assert "mintable" in score["flags"]
    assert "bundle_suspect" in score["flags"]
    assert "repeat_deployer" in score["flags"]
    assert "deployer is a known scammer" in score["reasons"]
    assert rs["risk_verdict_text"]
    assert "CRITICAL" in rs["risk_verdict_text"]


async def test_failing_module_degrades_to_none():
    """One module raising must not fail the scan; its fields become None."""
    patchers = _patch_all_modules(
        wash=patch(
            "app.domains.scanner.scan_enrich.detect_wash_trading",
            new=AsyncMock(side_effect=RuntimeError("wash provider down")),
        ),
    )
    for p in patchers.values():
        p.start()
    try:
        result = await enrich_scan(_scan_result(), TOKEN, "ethereum")
    finally:
        for p in patchers.values():
            p.stop()

    rs = result["risk_signals"]
    # The failed module degrades to None (unknown), not False (clean).
    assert rs["wash_trading"] is None
    assert rs["wash_signal"] is None
    # Every other module still landed.
    assert rs["bundled"] is True
    assert rs["known_scammer"] is True
    assert rs["mintable"] is True
    assert rs["rug_risk_score"]["score"] == 100


async def test_all_modules_failing_still_returns_scan_result():
    """Total enrichment failure is fail-open: scan result returned, no raise."""
    patchers = _patch_all_modules(
        bundle=patch("app.domains.scanner.scan_enrich.detect_bundle", new=AsyncMock(side_effect=RuntimeError("x"))),
        reputation=patch(
            "app.domains.scanner.scan_enrich.deployer_reputation", new=AsyncMock(side_effect=RuntimeError("x"))
        ),
        wash=patch("app.domains.scanner.scan_enrich.detect_wash_trading", new=AsyncMock(side_effect=RuntimeError("x"))),
        whale=patch("app.domains.scanner.scan_enrich.whale_flow", new=AsyncMock(side_effect=RuntimeError("x"))),
        authority=patch(
            "app.domains.scanner.scan_enrich.check_authority", new=AsyncMock(side_effect=RuntimeError("x"))
        ),
        crossref=patch("app.domains.scanner.scan_enrich.crossref_wallet", new=AsyncMock(side_effect=RuntimeError("x"))),
    )
    for p in patchers.values():
        p.start()
    try:
        result = await enrich_scan(_scan_result(), TOKEN, "ethereum")
    finally:
        for p in patchers.values():
            p.stop()

    rs = result["risk_signals"]
    for key in (
        "bundled",
        "deployer_repeat",
        "wash_trading",
        "whale_flow",
        "mintable",
        "freezable",
        "lp_locked",
        "known_scammer",
        "kol_handle",
    ):
        assert rs[key] is None, f"expected None for {key}"
    # The consolidated score degrades to clean/low — never raises.
    assert rs["rug_risk_score"]["score"] == 0
    assert rs["rug_risk_score"]["verdict"] == "low"


def _rich_risk_signals() -> dict[str, Any]:
    """A risk_signals dict in the shape enrich_scan produces (signals fired)."""
    return {
        "bundled": True,
        "deployer_repeat": True,
        "wash_trading": True,
        "whale_flow": True,
        "mintable": True,
        "freezable": False,
        "lp_locked": True,
        "known_scammer": True,
        "kol_handle": None,
        "bundle_signal": "bundled: 62% snipers",
        "reputation_signal": "deployer launched 12 tokens",
        "wash_signal": "wash trading: 40% self-trades",
        "whale_signal": "whales net selling",
        "authority_signal": "mintable",
        "crossref_signal": "deployer is known scammer @scammer1 (rug_pull)",
        "rug_risk_score": {
            "score": 100,
            "verdict": "critical",
            "flags": ["known_scammer", "mintable", "bundled"],
            "reasons": ["deployer is a known scammer", "token supply is mintable"],
        },
        "risk_verdict_text": "🔴 CRITICAL (100/100) — deployer is known scammer · mintable · bundled launch",
    }


def test_renderer_emits_risk_signals_section_when_present():
    report = _scan_result()
    report["risk_signals"] = _rich_risk_signals()

    out = format_scan_report(report)

    assert "🔍 <b>Risk Signals</b>" in out
    # Consolidated verdict line
    assert "CRITICAL (100/100) — deployer is known scammer · mintable · bundled launch" in out
    # Individual signal strings
    assert "bundled: 62% snipers" in out
    assert "deployer launched 12 tokens" in out
    assert "wash trading: 40% self-trades" in out
    assert "whales net selling" in out
    assert "mintable" in out
    assert "deployer is known scammer @scammer1 (rug_pull)" in out
    # Section sits between Security Assessment and Market Data.
    assert out.index("Security Assessment") < out.index("🔍 <b>Risk Signals</b>") < out.index("Market Data")


def test_renderer_omits_section_when_clean():
    # No risk_signals at all → omitted.
    out_absent = format_scan_report(_scan_result())
    assert "Risk Signals" not in out_absent

    # risk_signals present but no signals fired → omitted.
    clean = _scan_result()
    clean["risk_signals"] = {
        "bundled": None,
        "deployer_repeat": None,
        "wash_trading": None,
        "whale_flow": None,
        "mintable": None,
        "freezable": None,
        "lp_locked": None,
        "known_scammer": None,
        "kol_handle": None,
        "bundle_signal": None,
        "reputation_signal": None,
        "wash_signal": None,
        "whale_signal": None,
        "authority_signal": None,
        "crossref_signal": None,
        "rug_risk_score": {"score": 0, "verdict": "low", "flags": [], "reasons": []},
        "risk_verdict_text": "🟢 LOW (0/100) — no significant risk flags",
    }
    out_clean = format_scan_report(clean)
    assert "Risk Signals" not in out_clean
    assert "no significant risk flags" not in out_clean
