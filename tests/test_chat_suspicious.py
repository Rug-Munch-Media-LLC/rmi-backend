"""Pure unit tests for the "suspicious tokens" chat tool intent.

Covers ``app/domains/chat_tools.py``:
  - ``_detect_tool_intent`` returns "suspicious" for shady/new-token LIST
    requests and never hijacks single-address scans or definitional questions.
  - ``_exec_suspicious`` renders a numbered list of real launches ranked by
    the consolidated 25-point rug-risk score (honeypot/mintable/LP-lock/bundle
    + age/liquidity + known-scammer overlap), each line carrying
    ``score N/100 VERDICT — reasons``; falls back to ``_launch_suspicion_score``
    reasons when the consolidated scorer is unavailable or silent; returns
    None on empty data / failure (never fabricates tokens).
  - ``_run_tool_for_intent`` routes suspicious → ``_exec_suspicious``.

No network, DB, or LLM calls — the launch detector, GMGN provider, known
scammers loader, and rug-risk scorer are mocked at the module boundary.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any
from unittest import mock

import pytest

from app.domains import chat_tools
from app.domains.chat_tools import (
    _detect_tool_intent,
    _exec_suspicious,
    _rank_suspicious,
    _run_tool_for_intent,
)

_EVM = "0x" + "a" * 40
_SOL = "D8pom1W4v6j8kQkfQq2mNnXxYyZzAaBbCcDdEeFfGgHh"


def _launch(**overrides: Any) -> dict[str, Any]:
    """A launch record shaped like ``TokenLaunchDetector.get_recent_launches``."""
    base: dict[str, Any] = {
        "address": "LaunChAddr111111111111111111111111111111111",
        "chain": "solana",
        "symbol": "PEPE",
        "name": "Pepe Coin",
        "liquidity": 1200.0,
        "market_cap": 8000.0,
        "age_seconds": 240,
        "dex": "pump",
        "pair_address": "PairAddr1111111111111111111111111111111",
    }
    base.update(overrides)
    return base


@contextmanager
def _patched_sources(detector_launches: list[dict[str, Any]]):
    """Patch the detector / GMGN / scammer module boundaries for ``_exec_suspicious``.

    Yields ``(gmgn, scammers)``: ``gmgn`` is the mocked ``get_gmgn_provider``
    (set ``gmgn.return_value.get_new_pairs``), ``scammers`` the mocked
    ``load_known_scammers`` (set ``scammers.return_value`` for a known list).
    """
    detector = mock.MagicMock()
    detector.get_recent_launches = mock.AsyncMock(return_value=detector_launches)
    with ExitStack() as stack:
        stack.enter_context(mock.patch("app.databus.token_launch_detector.get_launch_detector", return_value=detector))
        gmgn = stack.enter_context(mock.patch("app.databus.gmgn_provider.get_gmgn_provider"))
        scammers = stack.enter_context(mock.patch("app.databus.shill_detector.load_known_scammers", return_value=set()))
        yield gmgn, scammers


def _fake_rug_risk_score(signals: dict[str, Any]) -> dict[str, Any]:
    """Deterministic stand-in for ``rug_risk_score`` over the signal subset
    ``_launch_rug_signals`` produces (mirrors the real scorer's weights)."""
    score = 0
    flags: list[str] = []
    reasons: list[str] = []
    if signals.get("honeypot"):
        score += 40
        flags.append("honeypot")
        reasons.append("contract is a honeypot")
    if signals.get("known_scammer"):
        score += 40
        flags.append("known_scammer")
        reasons.append("deployer is a known scammer")
    if signals.get("mintable"):
        score += 35
        flags.append("mintable")
        reasons.append("token supply is mintable")
    if signals.get("bundled"):
        score += 25
        flags.append("bundled")
        reasons.append("launch was bundled")
    if signals.get("lp_locked") is False:
        score += 22
        flags.append("lp_unlocked")
        reasons.append("LP is unlocked")
    try:
        liq = float(signals.get("liquidity")) if signals.get("liquidity") is not None else None
    except (TypeError, ValueError):
        liq = None
    if liq is not None and liq < 10_000:
        score += 12
        flags.append("low_liquidity")
        reasons.append("liquidity is very low")
    try:
        age = float(signals.get("age_seconds")) if signals.get("age_seconds") is not None else None
    except (TypeError, ValueError):
        age = None
    if age is not None and age < 3600:
        score += 12
        flags.append("very_young")
        reasons.append("token is very young")
    score = max(0, min(100, score))
    if "honeypot" in flags or "known_scammer" in flags:
        score = max(score, 70)
    verdict = "critical" if score >= 70 else "high" if score >= 45 else "medium" if score >= 25 else "low"
    return {"score": score, "verdict": verdict, "flags": flags, "reasons": reasons}


def _fake_verdict_text(result: dict[str, Any]) -> str:
    """Deterministic stand-in for ``risk_verdict_text`` — mirrors the real
    module's ``_FLAG_TEXT`` mapping (top-3 flags, no emoji)."""
    flag_text = {
        "honeypot": "honeypot",
        "known_scammer": "deployer is known scammer",
        "mintable": "mintable",
        "bundled": "bundled launch",
        "deployer_repeat": "repeat rug deployer",
        "lp_unlocked": "LP unlocked",
        "wash_trading": "wash trading",
        "whale_exit": "whale exit",
        "low_liquidity": "low liquidity",
        "very_young": "very young token",
        "kol_verified": "KOL verified",
    }
    score = int(result.get("score") or 0)
    verdict = str(result.get("verdict") or "low").upper()
    flags = [str(f) for f in (result.get("flags") or []) if str(f) != "kol_verified"][:3]
    if not flags:
        return f"{verdict} ({score}/100) — no significant risk flags"
    parts = " · ".join(flag_text.get(flag, flag.replace("_", " ")) for flag in flags)
    return f"{verdict} ({score}/100) — {parts}"


@contextmanager
def _patched_scorer():
    """Pin the consolidated scorer to a deterministic fake (no real module I/O)."""
    with (
        mock.patch("app.domains.chat_tools.rug_risk_score", new=_fake_rug_risk_score),
        mock.patch("app.domains.chat_tools.risk_verdict_text", new=_fake_verdict_text),
    ):
        yield


# ── 1. Intent classifier ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # suspicious — list/analysis asks about shady or new tokens
        ("shady solana tokens you noticed launching", "suspicious"),
        ("suspicious tokens", "suspicious"),
        ("recent rug launches", "suspicious"),
        ("list sketchy new tokens on solana", "suspicious"),
        ("any honeypot tokens launching?", "suspicious"),
        ("red flags on recent launches", "suspicious"),
        ("that pump's sus", "suspicious"),
        # definitional "what is X" questions stay with RAG, not suspicious
        ("what is a honeypot", None),
        ("what is a honeypot token", None),
        # single-address security queries stay scan (never suspicious)
        (f"scan {_EVM}", "scan"),
        (f"is {_SOL} a rug", "scan"),
        # unrelated intents are untouched
        ("how's the market", "market"),
        ("latest news", "news"),
    ],
)
def test_detect_suspicious_intent(message: str, expected: str | None) -> None:
    assert _detect_tool_intent(message) == expected


# ── 2. Executor rendering ─────────────────────────────────────────────


async def test_exec_suspicious_renders_numbered_list() -> None:
    launches = [
        _launch(symbol="RUG1", address="RugAddr11111111111111111111111111111111", liquidity=0.0, age_seconds=120),
        _launch(symbol="SUS2", address="SusAddr2222222222222222222222222222222222", liquidity=850.0, age_seconds=300),
        _launch(symbol="NEW3", address="NewAddr333333333333333333333333333333333", liquidity=12000.0, age_seconds=60),
    ]
    with _patched_sources(launches) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        with _patched_scorer():
            text = await _exec_suspicious()
    assert text is not None
    assert text.startswith("Suspicious recent launches:")
    assert "1. 🔴 RUG1 (solana) — RugAddr111… —" in text
    assert "score 24/100 LOW — low liquidity · very young token" in text
    assert "🔴 SUS2" in text
    assert "🔴 NEW3" in text
    assert "score 12/100 LOW — very young token" in text
    assert text.index("RUG1") < text.index("SUS2") < text.index("NEW3")


async def test_exec_suspicious_flags_known_scammer_deployer() -> None:
    launches = [
        _launch(
            symbol="SCAM",
            address="ScamAddr111111111111111111111111111111111",
            liquidity=50000.0,
            age_seconds=7200,
            creator="KnownScammerAddr11111111111111111111111111",
        )
    ]
    with _patched_sources(launches) as (gmgn, scammers):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        scammers.return_value = {"KnownScammerAddr11111111111111111111111111"}
        with _patched_scorer():
            text = await _exec_suspicious()
    assert text is not None
    assert "score 70/100 CRITICAL" in text
    assert "known scammer" in text


async def test_exec_suspicious_flags_honeypot_and_mintable() -> None:
    launches = [
        _launch(
            symbol="HONEY",
            address="HoneyAddr1111111111111111111111111111111",
            liquidity=30000.0,
            age_seconds=9000,
            is_honeypot=True,
            is_mintable=True,
        )
    ]
    with _patched_sources(launches) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        with _patched_scorer():
            text = await _exec_suspicious()
    assert text is not None
    assert "honeypot" in text
    assert "mintable" in text


async def test_exec_suspicious_supplements_with_gmgn_fresh_pairs() -> None:
    launches = [
        _launch(symbol="OLD", address="OldAddr111111111111111111111111111111111", liquidity=0.0, age_seconds=5000)
    ]
    fresh = [
        {
            "address": "FreshAddr111111111111111111111111111111111",
            "chain": "solana",
            "symbol": "FRESH",
            "name": "Fresh Token",
            "price": 0.0001,
            "liquidity": 500.0,
            "market_cap": 3000.0,
            "created_at": 0,
            "age_seconds": 0,
            "launchpad": "pump",
            "source": "gmgn",
        }
    ]
    with _patched_sources(launches) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=fresh)
        with _patched_scorer():
            text = await _exec_suspicious()
    assert text is not None
    assert "FRESH" in text
    assert "low liquidity" in text


async def test_exec_suspicious_dedupes_same_address_across_sources() -> None:
    shared = _launch(symbol="DUP", address="DupAddr1111111111111111111111111111111", liquidity=0.0, age_seconds=60)
    gmgn_dup = {**shared, "source": "gmgn", "launchpad": "pump"}
    with _patched_sources([shared]) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[gmgn_dup])
        with _patched_scorer():
            text = await _exec_suspicious()
    assert text is not None
    assert text.count("DUP") == 1


# ── 3. Executor fails open (never raises, never fabricates) ────────────


async def test_exec_suspicious_returns_none_on_empty_launches() -> None:
    with _patched_sources([]) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        assert await _exec_suspicious() is None


async def test_exec_suspicious_returns_none_on_detector_failure() -> None:
    with (
        mock.patch("app.databus.token_launch_detector.get_launch_detector", side_effect=RuntimeError("boom")),
        mock.patch("app.databus.gmgn_provider.get_gmgn_provider") as gmgn,
        mock.patch("app.databus.shill_detector.load_known_scammers", return_value=set()),
    ):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        assert await _exec_suspicious() is None


async def test_exec_suspicious_returns_none_when_nothing_is_shady() -> None:
    # Mature, well-liquidity launch records carry no concrete suspicion signal.
    launches = [
        _launch(symbol="SAFE", liquidity=50000.0, market_cap=1_000_000.0, age_seconds=7200),
        _launch(symbol="OKAY", liquidity=20000.0, market_cap=300_000.0, age_seconds=14400),
    ]
    with _patched_sources(launches) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        assert await _exec_suspicious() is None


# ── 3b. Consolidated rug-risk ranking + fallback ────────────────────────


async def test_exec_suspicious_ranks_honeypot_above_young_illiquid() -> None:
    # A confirmed honeypot (70 critical via the hard floor) outranks a young /
    # illiquid launch (24 low) even when the honeypot is mature and well-capitalized.
    launches = [
        _launch(symbol="YOUNG", address="YoungAddr111111111111111111111111111111", liquidity=0.0, age_seconds=60),
        _launch(
            symbol="HONEY",
            address="HoneyAddr2222222222222222222222222222222",
            liquidity=50000.0,
            market_cap=1_000_000.0,
            age_seconds=7200,
            security={
                "honeypot": True,
                "mintable": False,
                "lp_locked": None,
                "creator_address": None,
                "bundle_suspect": False,
            },
        ),
    ]
    with _patched_sources(launches) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        with _patched_scorer():
            text = await _exec_suspicious()
    assert text is not None
    assert text.index("HONEY") < text.index("YOUNG")
    assert "score 70/100 CRITICAL" in text
    assert "honeypot" in text
    assert "score 24/100 LOW" in text


async def test_exec_suspicious_line_includes_score_verdict_and_reasons() -> None:
    launches = [
        _launch(
            symbol="HONEY",
            address="HoneyAddr1111111111111111111111111111111",
            liquidity=50000.0,
            market_cap=1_000_000.0,
            age_seconds=7200,
            security={
                "honeypot": True,
                "mintable": True,
                "lp_locked": None,
                "creator_address": None,
                "bundle_suspect": True,
            },
        )
    ]
    with _patched_sources(launches) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        with _patched_scorer():
            text = await _exec_suspicious()
    assert text is not None
    assert "1. 🔴 HONEY (solana) — HoneyAddr1… — score 100/100 CRITICAL" in text
    assert "honeypot" in text
    assert "mintable" in text
    assert "bundled launch" in text


async def test_exec_suspicious_falls_back_to_legacy_score_when_scorer_unavailable() -> None:
    # rug_risk_score not importable → the legacy heuristic's reason strings drive the list.
    launches = [
        _launch(symbol="RUG1", address="RugAddr11111111111111111111111111111111", liquidity=0.0, age_seconds=120),
        _launch(symbol="SUS2", address="SusAddr2222222222222222222222222222222222", liquidity=850.0, age_seconds=300),
    ]
    with _patched_sources(launches) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        with mock.patch("app.domains.chat_tools.rug_risk_score", None):
            text = await _exec_suspicious()
    assert text is not None
    assert "0 liquidity" in text
    assert "LP $850" in text


async def test_exec_suspicious_falls_back_when_consolidated_scorer_silent() -> None:
    # Tiny market cap is NOT a rug_risk_score signal — the legacy heuristic
    # still surfaces it, so the record is ranked, not dropped.
    launches = [
        _launch(
            symbol="TINY",
            address="TinyAddr1111111111111111111111111111111",
            liquidity=20000.0,
            market_cap=1000.0,
            age_seconds=7200,
        )
    ]
    with _patched_sources(launches) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        with _patched_scorer():
            text = await _exec_suspicious()
    assert text is not None
    assert "TINY" in text
    assert "MC $1,000" in text


async def test_exec_suspicious_drops_records_with_no_reason_in_fallback() -> None:
    # "No concrete reason → drop" survives the fallback path too.
    launches = [
        _launch(symbol="SAFE", liquidity=50000.0, market_cap=1_000_000.0, age_seconds=7200),
    ]
    with _patched_sources(launches) as (gmgn, _):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        with mock.patch("app.domains.chat_tools.rug_risk_score", None):
            assert await _exec_suspicious() is None


def test_rank_suspicious_prefers_consolidated_score() -> None:
    honeypot = _launch(
        symbol="HONEY", liquidity=50000.0, age_seconds=7200, security={"honeypot": True, "lp_locked": None}
    )
    young = _launch(symbol="YOUNG", liquidity=0.0, age_seconds=60)
    with _patched_scorer():
        ranked = _rank_suspicious([young, honeypot], set())
    assert [launch["symbol"] for launch, _ in ranked] == ["HONEY", "YOUNG"]
    assert ranked[0][1].startswith("score 70/100 CRITICAL")
    assert ranked[1][1].startswith("score 24/100 LOW")


# ── 4. Dispatcher wiring ──────────────────────────────────────────────


def test_suspicious_intent_wired_to_executor() -> None:
    assert chat_tools._EXECUTORS["suspicious"] is _exec_suspicious


async def test_run_tool_for_intent_routes_suspicious() -> None:
    fake = mock.AsyncMock(return_value="Suspicious recent launches:\n1. 🔴 TEST")
    with mock.patch.dict(chat_tools._EXECUTORS, {"suspicious": fake}):
        result = await _run_tool_for_intent("suspicious tokens", "suspicious")
    assert result == "Suspicious recent launches:\n1. 🔴 TEST"
    fake.assert_awaited_once_with("suspicious tokens")
