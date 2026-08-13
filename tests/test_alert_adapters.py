"""Pure unit tests for the alerts adapter layer (app.domains.alerts.adapters).

Synthetic detector payloads per source -> canonical PlatformAlert field
assertions; malformed payloads -> None; batch filtering; and the
collect_pipeline_alerts orchestrator (adapt -> dedupe -> route). No network,
no DB.
"""

from __future__ import annotations

from typing import Any

from app.domains.alerts.adapters import (
    adapt_alert,
    adapt_batch,
    adapt_bulletin,
    adapt_launch,
    adapt_mempool,
    adapt_scanner,
    adapt_security_intel,
    adapt_syndicate,
    adapt_whale,
    collect_pipeline_alerts,
)
from app.domains.alerts.models import PlatformAlert

_TS = "2026-08-13T10:00:00+00:00"


# ── Per-source adapters ────────────────────────────────────────────────────


def test_adapt_whale_whale_alert_shape() -> None:
    raw: dict[str, Any] = {
        "timestamp": _TS,
        "alert_type": "whale_dump",
        "token_address": "So11111111111111111111111111111111111111112",
        "token_symbol": "SOL",
        "from_wallet": "aaa",
        "to_wallet": "bbb",
        "amount_tokens": 5000.0,
        "amount_usd": 250000.0,
        "pct_of_supply": 3.2,
        "severity": 5,  # int 1-5 -> critical
        "tx_signature": "sig123",
        "whale_tags": ["accumulator"],
    }
    alert = adapt_whale(raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "whale"
    assert alert.severity == "critical"
    assert alert.title == "whale_dump"
    assert alert.token_address == raw["token_address"]
    assert alert.chain == "solana"
    assert alert.source == "whale"
    assert alert.event_key  # derived deterministically


def test_adapt_whale_scan_alert_shape() -> None:
    raw: dict[str, Any] = {
        "type": "extreme_concentration",
        "wallet": "walletAddr",
        "pct": 42.5,
        "message": "Wallet holds 42.5% of total supply!",
    }
    alert = adapt_whale(raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "whale"
    assert alert.severity == "high"  # fallback default for whale
    assert alert.title == raw["message"]
    assert alert.token_address == "walletAddr"


def test_adapt_syndicate_generated_alert() -> None:
    raw: dict[str, Any] = {
        "severity": "critical",
        "type": "extreme_concentration",
        "message": "Syndicate controls 95.0% of supply",
        "token_address": "Eme5T2s2HB7B8W4YgLG1eReQpnadEVUnQBRjaKTdBAGS",
    }
    alert = adapt_syndicate(raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "syndicate"
    assert alert.severity == "critical"
    assert alert.title == raw["message"]
    assert alert.token_address == raw["token_address"]


def test_adapt_syndicate_event_risk_flag() -> None:
    raw: dict[str, Any] = {
        "risk_flag": "high",
        "event_type": "transfer",
        "description": "CRM V1 transfer: 100.00 tokens",
        "token_address": "Eme5T2s2HB7B8W4YgLG1eReQpnadEVUnQBRjaKTdBAGS",
        "timestamp": _TS,
    }
    alert = adapt_syndicate(raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "syndicate"
    assert alert.severity == "high"
    assert alert.title == raw["description"]


def test_adapt_mempool() -> None:
    raw: dict[str, Any] = {
        "title": "Frontrun detected on Uniswap V3 pool",
        "severity": "high",
        "address": "0xPool",
        "chain": "base",
    }
    alert = adapt_mempool(raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "mempool"
    assert alert.severity == "high"
    assert alert.title == raw["title"]
    assert alert.token_address == "0xPool"
    assert alert.chain == "base"


def test_adapt_launch_high_risk_alert() -> None:
    raw: dict[str, Any] = {
        "type": "new_launch_high_risk",
        "address": "TokenMintAddr123456789",
        "symbol": "RUG",
        "name": "TestCoin",
        "risk_score": 85,
        "risk_flags": ["honeypot_indicator", "low_liquidity"],
        "liquidity": 500,
        "price": 0.0001,
        "chain": "solana",
        "timestamp": _TS,
    }
    alert = adapt_launch(raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "launch"
    assert alert.severity == "high"  # risk 85 -> high
    assert alert.title == "TestCoin"
    assert alert.token_address == raw["address"]
    assert "honeypot_indicator" in alert.body


def test_adapt_scanner_article() -> None:
    raw: dict[str, Any] = {
        "id": "rmi-abc123",
        "title": "RMI Scanner: ShadyToken (SOL) - Risk 65/100",
        "url": "https://rugmunch.io/scanner?address=x&chain=solana",
        "description": "Scanner detected ShadyToken on solana. Risk: 65/100.",
        "source": "RMI Scanner",
        "published_at": _TS,
        "category": "security",
        "kind": "internal",
        "risk_score": 65,
        "token_address": "ShadyMint111111111111111111111111111111111",
        "chain": "solana",
    }
    alert = adapt_scanner(raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "scanner"
    assert alert.severity == "medium"  # risk 65 -> medium
    assert alert.title == raw["title"]
    assert alert.token_address == raw["token_address"]
    assert alert.urls == [raw["url"]]


def test_adapt_bulletin_alert_in() -> None:
    raw: dict[str, Any] = {
        "title": "Exchange maintenance window",
        "description": "Withdrawals paused 2h.",
        "severity": "low",
        "alert_type": "security",
        "chain": "ethereum",
        "token": "0xToken",
        "wallet": "0xWallet",
        "amount_usd": 1000.0,
    }
    alert = adapt_bulletin(raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "bulletin"
    assert alert.severity == "low"
    assert alert.title == raw["title"]
    assert alert.token_address == "0xToken"
    assert alert.chain == "ethereum"


def test_adapt_security_intel_detection_wrapped() -> None:
    raw: dict[str, Any] = {
        "id": "alert_abc123",
        "detection": {
            "type": "honeypot_detected",
            "message": "Contract flagged as honeypot",
            "address": "0xContract",
            "chain": "base",
        },
        "status": "pending",
        "created_at": _TS,
    }
    alert = adapt_security_intel(raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "honeypot"
    assert alert.severity == "high"  # fallback default for security-intel
    assert alert.title == "Contract flagged as honeypot"
    assert alert.token_address == "0xContract"
    assert alert.chain == "base"
    assert alert.source == "security-intel"
    assert alert.ts == _TS  # pulled from wrapper, not the inner detection


# ── Dispatcher ─────────────────────────────────────────────────────────────


def test_dispatcher_honors_source_alias_security_intel() -> None:
    raw: dict[str, Any] = {"alert_type": "rug_pull_risk", "severity": "critical", "message": "LP removed"}
    alert = adapt_alert("security_intel", raw)
    assert isinstance(alert, PlatformAlert)
    assert alert.category == "rug-risk"
    assert alert.severity == "critical"
    assert alert.source == "security-intel"


def test_dispatcher_unknown_source_returns_none() -> None:
    assert adapt_alert("unknown_source", {"title": "x"}) is None
    assert adapt_alert("", {"title": "x"}) is None


# ── Malformed rejection ────────────────────────────────────────────────────


def test_malformed_returns_none_per_source() -> None:
    assert adapt_whale({}) is None
    assert adapt_syndicate({"severity": "critical"}) is None  # no title/message/type
    assert adapt_mempool({"severity": "high"}) is None  # no title/message/type
    assert adapt_launch({}) is None  # no address/token
    assert adapt_scanner({"risk_score": 90}) is None  # no title
    assert adapt_bulletin({"severity": "medium"}) is None  # no title
    assert adapt_security_intel({"severity": "high"}) is None  # no title/message/type
    assert adapt_alert("whale", None) is None  # type: ignore[arg-type]
    assert adapt_alert("whale", "not a dict") is None  # type: ignore[arg-type]


# ── Batch ──────────────────────────────────────────────────────────────────


def test_adapt_batch_filters_malformed() -> None:
    good: dict[str, Any] = {
        "alert_type": "whale_dump",
        "severity": 4,
        "token_address": "mint1",
        "timestamp": _TS,
    }
    batch = [good, {}, {"severity": 4}, good]
    adapted = adapt_batch("whale", batch)
    assert len(adapted) == 2
    assert all(isinstance(a, PlatformAlert) for a in adapted)
    assert {a.token_address for a in adapted} == {"mint1"}


# ── Pipeline orchestrator: dedupe + route ──────────────────────────────────


def _plan_channel_counts(source_outputs: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    plan = collect_pipeline_alerts(source_outputs)
    return {entry["channel"]: entry["count"] for entry in plan}


def test_collect_pipeline_alerts_dedupes_and_routes() -> None:
    source_outputs: dict[str, list[dict[str, Any]]] = {
        "scanner": [
            {"title": "Scan A", "risk_score": 85, "token_address": "mintS", "chain": "solana", "timestamp": _TS},
            # exact duplicate (same entity + source + ts bucket) -> collapsed by event_key
            {"title": "Scan A", "risk_score": 85, "token_address": "mintS", "chain": "solana", "timestamp": _TS},
            {"risk_score": 90},  # malformed -> dropped
        ],
        "whale": [{"alert_type": "whale_dump", "severity": 4, "token_address": "mintW", "timestamp": _TS}],
        "launch": [{"address": "mintL", "risk_score": 95, "name": "RugCoin", "timestamp": _TS}],
    }
    counts = _plan_channel_counts(source_outputs)

    # 3 distinct alerts after dedupe: scanner high, whale high, launch critical
    assert counts["site"] == 3
    # high+ routes to rmialerts and alerts_channel (scanner + whale + launch)
    assert counts["rmialerts"] == 3
    assert counts["alerts_channel"] == 3
    # scanner + whale + launch are all news categories
    assert counts["rmicryptonews"] == 3
    # critical-only for main channel (launch risk 95 -> critical)
    assert counts["main_channel"] == 1


def test_collect_pipeline_alerts_main_channel_critical_only() -> None:
    source_outputs: dict[str, list[dict[str, Any]]] = {
        "whale": [{"alert_type": "accumulation", "severity": 3, "token_address": "mintH", "timestamp": _TS}],
        "bulletin": [{"title": "Maintenance", "severity": "medium", "timestamp": _TS}],
        "security_intel": [
            {
                "detection": {
                    "type": "exploit_detected",
                    "message": "Exploit",
                    "address": "0xE",
                    "severity": "critical",
                },
                "created_at": _TS,
            }
        ],
    }
    plan = collect_pipeline_alerts(source_outputs)
    plan_by_channel = {entry["channel"]: entry["alerts"] for entry in plan}

    main_titles = {a["title"] for a in plan_by_channel["main_channel"]}
    assert main_titles == {"Exploit"}  # only the critical security-intel alert

    rmialert_sources = {a["source"] for a in plan_by_channel["rmialerts"]}
    # whale severity 3 -> medium, bulletin medium: NOT routed to rmialerts (high+)
    assert rmialert_sources == {"security-intel"}

    site_sources = {a["source"] for a in plan_by_channel["site"]}
    assert site_sources == {"whale", "bulletin", "security-intel"}  # everything lands on site
