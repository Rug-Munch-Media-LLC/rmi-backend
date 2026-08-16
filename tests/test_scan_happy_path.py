"""Happy-path tests for the /scan user journey.

Covers the core scanner surface with every external provider mocked:

  * ``scan_token`` — a normal token returns a result with a sane verdict
    (safety_score + derived risk level), metrics (confidence, modules_run,
    fusion brief) and risk flags; a honeypot token is flagged high-risk.
  * ``format_scan_report`` — renders the canonical 4-part report
    (verdict / key metrics / evidence / watch).

No network, DB, Redis or Telegram calls are made: module providers, data
fusion, RAG ingestion and scanner alerts are all patched (mirrors
``tests/test_scanner_service.py``).
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

from app.domains.scanner.service.scoring import _risk_level_from_safety
from app.domains.telegram.rugmunchbot.formatters import format_scan_report

# Module provider names patched inside ``app.domains.scanner.service.scan``
# (the FREE tier task list built by ``_build_module_tasks``).
_FREE_MODULES = (
    "fetch_market_data",
    "_check_honeypot_is",
    "_check_goplus_security",
    "_check_copycat",
    "_check_spam",
    "_check_blockscout",
    "_check_defi_scanner",
    "_check_token_age",
    "_check_volume_liquidity_ratio",
)


# ── Fixtures / builders ───────────────────────────────────────────────────


def _clean_modules() -> dict[str, dict]:
    """Provider payloads for a normal, low-risk token (all mocked)."""
    return {
        "fetch_market_data": {
            "price": 0.0123,
            "volume_24h": 250_000,
            "liquidity": 50_000,
            "result": "scanned",
            "source": "dexscreener",
        },
        "_check_honeypot_is": {"result": "scanned", "is_honeypot": False, "risk_score": 5},
        "_check_goplus_security": {
            "result": "scanned",
            "is_honeypot": False,
            "risk_score": 5,
            "is_spam": False,
            "is_sanctioned": False,
        },
        "_check_copycat": {"result": "clean"},
        "_check_spam": {"result": "scanned", "is_spam": False},
        "_check_blockscout": {"result": "pending"},
        "_check_defi_scanner": {"result": "scanned", "risk_score": 5, "liquidity_usd": 50_000},
        "_check_token_age": {"result": "scanned", "is_new_token": False, "risk_level": "ok"},
        "_check_volume_liquidity_ratio": {
            "result": "scanned",
            "is_wash_trading_suspicious": False,
            "risk_level": "ok",
        },
    }


def _honeypot_modules() -> dict[str, dict]:
    """Provider payloads for a honeypot token (high risk)."""
    modules = _clean_modules()
    modules["_check_honeypot_is"] = {"result": "scanned", "is_honeypot": True, "risk_score": 90}
    modules["_check_goplus_security"] = {
        "result": "scanned",
        "is_honeypot": True,
        "risk_score": 85,
        "is_spam": False,
        "is_sanctioned": False,
    }
    return modules


def _fake_enricher() -> Mock:
    """RAGIntelligenceEnricher fake returning an empty RAG context."""
    from types import SimpleNamespace

    enricher = Mock()
    ctx = SimpleNamespace(
        similar_scams=[],
        wallet_context=[],
        market_context=[],
        fusion_summary="",
        collections_searched=[],
        took_ms=0,
        query_id="test",
    )
    enricher.enrich_scan_result = AsyncMock(return_value=ctx)
    return enricher


def _patch_scan_providers(modules: dict[str, dict], *, fused_value: float = 15.0) -> ExitStack:
    """Enter every patch needed for a hermetic ``scan_token`` run.

    Patching the ``app.core.data_fusion.fusion`` singleton methods works
    because ``scan.py`` binds the same object at import time
    (``from app.core.data_fusion import fusion``).
    """
    stack = ExitStack()
    for name in _FREE_MODULES:
        stack.enter_context(
            patch(f"app.domains.scanner.service.scan.{name}", new=AsyncMock(return_value=modules[name]))
        )
    stack.enter_context(patch("app.domains.scanner.service.scan._ingest_scan_to_rag", new=AsyncMock()))
    # Never fire the Telegram scanner alert in tests.
    stack.enter_context(
        patch("app.domains.telegram.alert_hooks.should_send_scanner_alert", new=Mock(return_value=False))
    )
    stack.enter_context(
        patch(
            "app.core.data_fusion.fusion.fuse_token_risk",
            new=AsyncMock(
                return_value={
                    "fused_value": fused_value,
                    "confidence": 0.8,
                    "sources_used": ["goplus"],
                    "risk_findings": [],
                }
            ),
        )
    )
    stack.enter_context(
        patch(
            "app.core.data_fusion.fusion.generate_intelligence_brief",
            new=Mock(return_value={"summary": "Fused test brief", "risk_score": fused_value}),
        )
    )
    stack.enter_context(patch("app.rag.intelligence.RAGIntelligenceEnricher", new=Mock(return_value=_fake_enricher())))
    return stack


def _report_dict() -> dict:
    """A rich scan report dict exercising every section of the 4-part layout."""
    return {
        "address": "0xabc123",
        "chain": "ethereum",
        "name": "Test Token",
        "symbol": "TST",
        "price": 0.0123,
        "mcap": 1_000_000,
        "liquidity": 50_000,
        "volume_24h": 100_000,
        "price_change_1h": 1.0,
        "price_change_24h": 5.0,
        "price_change_7d": 10.0,
        "risk_score": 60,
        "honeypot": False,
        "contract_verified": True,
        "mintable": False,
        "owner_renounced": True,
        "lp_locked": 0.9,
        "buy_tax": 0.05,
        "sell_tax": 0.07,
        "volume_real_ratio": 1.5,
        "deployer_address": "0xdeployer",
        "top_holders": [
            {"address": "0xaaa", "pct": 40.0, "tag": "Dev", "is_contract": False},
            {"address": "0xbbb", "pct": 20.0, "tag": None, "is_contract": True},
        ],
        "socials": {"twitter": "https://x.com/tst", "telegram": "https://t.me/tst"},
        "threats": [],
    }


# ── scan_token happy path ─────────────────────────────────────────────────


async def test_scan_token_clean_token_returns_verdict_and_metrics() -> None:
    with _patch_scan_providers(_clean_modules()):
        from app.domains.scanner.service import scan_token

        result = await scan_token("0xTokenClean", "ethereum", tiers=["free"])

    # Identity / metrics
    assert result.token_address == "0xTokenClean"
    assert result.chain == "ethereum"
    assert isinstance(result.safety_score, int)
    assert 0 <= result.safety_score <= 100
    assert isinstance(result.confidence, int)
    assert 0 <= result.confidence <= 100
    assert isinstance(result.risk_flags, list)
    assert "honeypot" not in result.risk_flags
    # Every FREE-tier module ran (9) and most returned real data.
    assert len(result.modules_run) >= 9
    ok_modules = [m for m in result.modules_run if m.get("status") == "success"]
    assert len(ok_modules) >= 5
    # Fusion enrichment produced a brief.
    assert result.fusion_brief is not None
    assert result.fusion_brief.get("summary") == "Fused test brief"
    # Sane verdict: a clean token is never high/critical risk.
    assert _risk_level_from_safety(float(result.safety_score)) not in ("high", "critical")


async def test_scan_token_honeypot_profile_is_high_risk() -> None:
    with _patch_scan_providers(_honeypot_modules(), fused_value=90.0):
        from app.domains.scanner.service import scan_token

        result = await scan_token("0xHoneypot", "ethereum", tiers=["free"])

    assert result.safety_score < 40
    assert "honeypot" in result.risk_flags
    # Sane verdict: a honeypot is high or critical risk, never safe.
    assert _risk_level_from_safety(float(result.safety_score)) in ("high", "critical")


# ── format_scan_report happy path (4-part layout) ─────────────────────────


def test_format_scan_report_renders_four_part_report() -> None:
    out = format_scan_report(_report_dict())
    assert isinstance(out, str)
    # 1. Verdict — brand header + risk headline
    assert "Rug Munch Token Scan" in out
    assert "Test Token" in out
    # 2. Key metrics — market table with real figures
    assert "Market Data" in out
    assert "Price:" in out
    assert "Market Cap:" in out
    assert "24h Volume:" in out
    # 3. Evidence — security assessment checks
    assert "Security Assessment" in out
    assert "Honeypot" in out
    assert "Verified" in out
    # 4. Watch — the actionable one-liner
    assert "Watch:" in out


async def test_scan_report_integration_from_scan_token() -> None:
    """scan_token → report dict → format_scan_report end to end (no crash)."""
    with _patch_scan_providers(_clean_modules()):
        from app.domains.scanner.service import scan_token

        result = await scan_token("0xTokenClean", "ethereum", tiers=["free"])

    report = {
        "address": result.token_address,
        "chain": result.chain,
        "name": "Clean Token",
        "symbol": "CLN",
        "risk_score": 100 - result.safety_score,
        "price": 0.0123,
        "mcap": 1_000_000,
        "liquidity": 50_000,
        "volume_24h": 250_000,
        "price_change_1h": 1.0,
        "price_change_24h": 5.0,
        "price_change_7d": 8.0,
        "honeypot": False,
        "contract_verified": True,
        "mintable": False,
        "owner_renounced": True,
        "lp_locked": 0.9,
        "socials": {"twitter": "https://x.com/cln"},
        "threats": [],
    }
    out = format_scan_report(report)
    assert "Rug Munch Token Scan" in out
    assert "Clean Token" in out
    assert "Market Data" in out
    assert "Security Assessment" in out
