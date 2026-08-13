"""Pure unit tests for the canonical platform-alert pipeline.

Covers: normalization for every source branch, malformed rejection,
event_key determinism, dedupe collapsing, routing severity thresholds +
category allowlists, main-channel critical-only, and the end-to-end
ingest_and_route orchestrator. No network, no DB.
"""

from __future__ import annotations

import pytest

from app.domains.alerts.ingestion import dedupe_alerts, normalize_alert
from app.domains.alerts.models import PlatformAlert
from app.domains.alerts.publisher import (
    DEFAULT_CHANNELS,
    NEWS_CATEGORIES,
    ChannelPolicy,
    route_alerts,
)
from app.domains.alerts.service import ingest_and_route

# ── Normalization: one branch per source ───────────────────────────────────


def test_normalize_whale_alert_shape() -> None:
    raw = {
        "alert_type": "whale_dump",
        "token_address": "So11111111111111111111111111111111111111112",
        "token_symbol": "SOL",
        "from_wallet": "aaa",
        "to_wallet": "bbb",
        "amount_tokens": 5000.0,
        "amount_usd": 250000.0,
        "pct_of_supply": 3.2,
        "severity": 5,
        "tx_signature": "sig123",
        "timestamp": "2026-08-13T10:00:00+00:00",
        "whale_tags": ["accumulator"],
    }
    alert = normalize_alert("whale", raw)
    assert alert is not None
    assert alert.category == "whale"
    assert alert.source == "whale"
    assert alert.severity == "critical"  # int 5 → critical
    assert alert.token_address == raw["token_address"]
    assert alert.chain == "solana"
    assert alert.title == "whale_dump"
    assert alert.event_key  # derived


def test_normalize_whale_scan_alert_shape() -> None:
    raw = {
        "type": "extreme_concentration",
        "wallet": "walletAddr",
        "pct": 42.5,
        "message": "Wallet holds 42.5% of total supply!",
    }
    alert = normalize_alert("whale", raw)
    assert alert is not None
    assert alert.category == "whale"
    assert alert.title == "Wallet holds 42.5% of total supply!"
    assert alert.token_address == "walletAddr"


def test_normalize_syndicate() -> None:
    raw = {
        "severity": "critical",
        "type": "extreme_concentration",
        "message": "Syndicate controls 95.0% of supply",
        "token_address": "Eme5T2s2HB7B8W4YgLG1eReQpnadEVUnQBRjaKTdBAGS",
    }
    alert = normalize_alert("syndicate", raw)
    assert alert is not None
    assert alert.category == "syndicate"
    assert alert.severity == "critical"
    assert alert.chain == "solana"
    assert alert.token_address == raw["token_address"]


def test_normalize_syndicate_risk_flag_shape() -> None:
    raw = {
        "risk_flag": "high",
        "event_type": "transfer",
        "description": "CRM V1 transfer: 100.00 tokens",
        "tx_signature": "sig",
    }
    alert = normalize_alert("syndicate", raw)
    assert alert is not None
    assert alert.severity == "high"
    assert alert.category == "syndicate"


def test_normalize_mempool() -> None:
    raw = {
        "title": "Frontrun detected on Uniswap V3 pool",
        "severity": "high",
        "address": "0xPool",
        "chain": "base",
    }
    alert = normalize_alert("mempool", raw)
    assert alert is not None
    assert alert.category == "mempool"
    assert alert.severity == "high"
    assert alert.token_address == "0xPool"
    assert alert.chain == "base"


def test_normalize_launch() -> None:
    raw = {
        "address": "TokenMintAddr123456789",
        "risk_score": 85,
        "risk_flags": ["honeypot_indicator", "low_liquidity"],
        "chain": "solana",
        "name": "TestCoin",
    }
    alert = normalize_alert("launch", raw)
    assert alert is not None
    assert alert.category == "launch"
    assert alert.severity == "high"  # 85 → high
    assert alert.token_address == raw["address"]
    assert "honeypot_indicator" in alert.body


def test_normalize_scanner() -> None:
    raw = {
        "title": "RMI Scanner: ShadyToken (SOL) - Risk 65/100",
        "description": "Scanner detected ShadyToken on solana.",
        "risk_score": 65,
        "token_address": "ShadyMint111111111111111111111111111111111",
        "chain": "solana",
        "url": "https://rugmunch.io/scanner?address=x&chain=solana",
        "published_at": "2026-08-13T10:00:00+00:00",
    }
    alert = normalize_alert("scanner", raw)
    assert alert is not None
    assert alert.category == "scanner"
    assert alert.severity == "medium"  # 65 → medium
    assert alert.urls == [raw["url"]]


def test_normalize_bulletin() -> None:
    raw = {
        "title": "Exchange maintenance window",
        "description": "Withdrawals paused 2h.",
        "severity": "low",
        "token": "0xToken",
        "chain": "ethereum",
    }
    alert = normalize_alert("bulletin", raw)
    assert alert is not None
    assert alert.category == "bulletin"
    assert alert.severity == "low"
    assert alert.token_address == "0xToken"
    assert alert.chain == "ethereum"


def test_normalize_security_intel_detection_wrapped() -> None:
    raw = {
        "id": "alert_abc123",
        "detection": {
            "type": "honeypot_detected",
            "message": "Contract flagged as honeypot",
            "address": "0xContract",
            "chain": "base",
        },
        "status": "pending",
        "created_at": "2026-08-13T10:00:00+00:00",
    }
    alert = normalize_alert("security-intel", raw)
    assert alert is not None
    assert alert.category == "honeypot"
    assert alert.severity == "high"  # default for security-intel
    assert alert.token_address == "0xContract"
    assert alert.source == "security-intel"


def test_normalize_security_intel_flat_rug() -> None:
    raw = {"alert_type": "rug_pull_risk", "severity": "critical", "message": "LP removed"}
    alert = normalize_alert("security-intel", raw)
    assert alert is not None
    assert alert.category == "rug-risk"
    assert alert.severity == "critical"


# ── Malformed rejection ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "raw"),
    [
        ("whale", {}),
        ("whale", None),
        ("whale", "not a dict"),
        ("syndicate", {"severity": "critical"}),  # no message/title/type
        ("mempool", {"severity": "high"}),  # no title/message/type
        ("launch", {}),  # no address/token
        ("scanner", {"risk_score": 90}),  # no title
        ("bulletin", {"severity": "medium"}),  # no title
        ("security-intel", {"severity": "high"}),  # no title/message/type
        ("unknown_source", {"title": "x"}),  # unsupported source
        ("", {"title": "x"}),  # empty source
    ],
)
def test_normalize_returns_none_on_malformed(source: str, raw: object) -> None:
    assert normalize_alert(source, raw) is None  # type: ignore[arg-type]


# ── event_key determinism ──────────────────────────────────────────────────


def test_event_key_is_deterministic() -> None:
    a = normalize_alert("scanner", {"title": "T", "risk_score": 80, "token_address": "mint1", "chain": "solana"})
    b = normalize_alert("scanner", {"title": "T", "risk_score": 80, "token_address": "mint1", "chain": "solana"})
    assert a is not None
    assert b is not None
    assert a.event_key == b.event_key
    assert a.derive_event_key() == b.derive_event_key()


def test_event_key_differs_by_source() -> None:
    a = normalize_alert("scanner", {"title": "Same", "token_address": "mint1"})
    b = normalize_alert("launch", {"address": "mint1", "risk_score": 80, "title": "Same"})
    assert a is not None
    assert b is not None
    assert a.event_key != b.event_key


def test_event_key_differs_across_ts_buckets() -> None:
    a = PlatformAlert(
        severity="high",
        category="whale",
        title="Whale move",
        token_address="mint1",
        source="whale",
        ts="2026-08-13T10:00:00+00:00",
    )
    b = PlatformAlert(
        severity="high",
        category="whale",
        title="Whale move",
        token_address="mint1",
        source="whale",
        ts="2026-08-13T11:00:00+00:00",  # different 5-min bucket
    )
    assert a.event_key != b.event_key


def test_event_key_same_bucket_same_key() -> None:
    a = PlatformAlert(
        severity="high",
        category="whale",
        title="Whale move",
        token_address="mint1",
        source="whale",
        ts="2026-08-13T10:01:00+00:00",
    )
    b = PlatformAlert(
        severity="high",
        category="whale",
        title="Whale move",
        token_address="mint1",
        source="whale",
        ts="2026-08-13T10:03:00+00:00",  # same 5-min bucket
    )
    assert a.event_key == b.event_key


# ── Dedupe ─────────────────────────────────────────────────────────────────


def test_dedupe_collapses_duplicates_preserving_order() -> None:
    one = PlatformAlert(
        severity="high", category="scanner", title="A", source="scanner", ts="2026-08-13T10:00:00+00:00"
    )
    two = PlatformAlert(
        severity="high", category="scanner", title="A", source="scanner", ts="2026-08-13T10:02:00+00:00"
    )
    three = PlatformAlert(
        severity="critical", category="exploit", title="B", source="security-intel", ts="2026-08-13T10:00:00+00:00"
    )
    out = dedupe_alerts([one, two, three])
    assert len(out) == 2
    assert [a.title for a in out] == ["A", "B"]


def test_dedupe_empty() -> None:
    assert dedupe_alerts([]) == []


# ── Routing: severity thresholds ───────────────────────────────────────────


def _plan_for(alert: PlatformAlert) -> dict[str, list[PlatformAlert]]:
    plan = route_alerts([alert])
    return {entry["channel"]: entry["alerts"] for entry in plan}


def test_route_site_receives_everything() -> None:
    low = PlatformAlert(severity="low", category="bulletin", title="Low", source="bulletin")
    plan = _plan_for(low)
    assert len(plan["site"]) == 1


def test_route_alerts_channel_high_and_above() -> None:
    high = PlatformAlert(severity="high", category="whale", title="Whale", source="whale")
    low = PlatformAlert(severity="low", category="bulletin", title="Low", source="bulletin")
    high_plan = _plan_for(high)
    low_plan = _plan_for(low)
    assert len(high_plan["alerts_channel"]) == 1
    assert len(low_plan["alerts_channel"]) == 0


def test_route_rmialerts_critical_and_high() -> None:
    critical = PlatformAlert(severity="critical", category="exploit", title="Exploit", source="security-intel")
    medium = PlatformAlert(severity="medium", category="bulletin", title="Med", source="bulletin")
    assert len(_plan_for(critical)["rmialerts"]) == 1
    assert len(_plan_for(medium)["rmialerts"]) == 0


def test_route_main_channel_critical_only() -> None:
    critical = PlatformAlert(severity="critical", category="rug-risk", title="Rug", source="security-intel")
    high = PlatformAlert(severity="high", category="whale", title="Whale", source="whale")
    medium = PlatformAlert(severity="medium", category="launch", title="Launch", source="launch")
    low = PlatformAlert(severity="low", category="bulletin", title="Low", source="bulletin")
    assert len(_plan_for(critical)["main_channel"]) == 1
    assert len(_plan_for(high)["main_channel"]) == 0
    assert len(_plan_for(medium)["main_channel"]) == 0
    assert len(_plan_for(low)["main_channel"]) == 0


# ── Routing: category allowlists ───────────────────────────────────────────


def test_route_rmicryptonews_respects_category_allowlist() -> None:
    scanner = PlatformAlert(severity="medium", category="scanner", title="Scan", source="scanner")
    launch = PlatformAlert(severity="medium", category="launch", title="Launch", source="launch")
    exploit = PlatformAlert(severity="critical", category="exploit", title="Exploit", source="security-intel")
    assert len(_plan_for(scanner)["rmicryptonews"]) == 1
    assert len(_plan_for(launch)["rmicryptonews"]) == 1
    assert len(_plan_for(exploit)["rmicryptonews"]) == 0


def test_news_categories_defined() -> None:
    assert {"scanner", "launch", "bulletin", "whale", "cex_flow"} <= NEWS_CATEGORIES


def test_route_respects_custom_channel_filter() -> None:
    policy = ChannelPolicy(min_severity="high", categories=frozenset({"honeypot"}))
    alert = PlatformAlert(severity="high", category="honeypot", title="HP", source="security-intel")
    plan = route_alerts([alert], channel_filter={"honeypot_alert": policy})
    assert len(plan) == 1
    assert plan[0]["channel"] == "honeypot_alert"
    assert plan[0]["count"] == 1

    wrong_cat = PlatformAlert(severity="high", category="whale", title="W", source="whale")
    plan = route_alerts([wrong_cat], channel_filter={"honeypot_alert": policy})
    assert plan[0]["count"] == 0


def test_route_plan_includes_noop_send_stub_by_default() -> None:
    plan = route_alerts([])
    assert plan
    assert callable(plan[0]["send"])


def test_default_channels_cover_expected_surfaces() -> None:
    assert set(DEFAULT_CHANNELS) == {
        "site",
        "alerts_channel",
        "rmialerts",
        "rmialpha",
        "rmicryptonews",
        "main_channel",
    }


# ── to_dict shape ──────────────────────────────────────────────────────────


def test_platform_alert_to_dict_shape() -> None:
    alert = PlatformAlert(severity="high", category="whale", title="T", body="B", source="whale")
    d = alert.to_dict()
    assert set(d) == {
        "id",
        "severity",
        "category",
        "title",
        "body",
        "token_address",
        "chain",
        "source",
        "event_key",
        "confidence",
        "ts",
        "urls",
    }
    assert d["severity"] == "high"
    assert d["category"] == "whale"
    assert d["event_key"]


# ── End-to-end orchestrator ────────────────────────────────────────────────


def test_ingest_and_route_end_to_end() -> None:
    raw = [
        ("scanner", {"title": "Scan A", "risk_score": 85, "token_address": "mint1", "chain": "solana"}),
        ("scanner", {"title": "Scan A", "risk_score": 85, "token_address": "mint1", "chain": "solana"}),  # dup
        (
            "whale",
            {
                "alert_type": "whale_dump",
                "severity": 5,
                "token_address": "mint2",
                "timestamp": "2026-08-13T10:00:00+00:00",
            },
        ),
        ("bulletin", {"title": "Maintenance", "severity": "low"}),
        ("launch", {"risk_score": 95, "address": "mint3", "name": "RugCoin"}),
        ("security-intel", {"type": "honeypot_detected", "message": "Honeypot", "severity": "high"}),
        ("mempool", {"title": "Bad"}),  # malformed-ish? has title → fine
    ]
    result = ingest_and_route(raw)
    assert result["received"] == 7
    assert result["normalized_count"] == 7
    assert result["dropped_count"] == 0
    assert result["deduped_count"] == 6  # one scanner dup collapsed
    assert result["duplicates_removed"] == 1
    plan = {entry["channel"]: entry["count"] for entry in result["plan"]}
    assert plan["site"] == 6
    # high+ (alerts_channel / rmialerts): scanner(high) + whale(critical) +
    # launch(critical) + intel(high) + mempool(high) = 5
    assert plan["alerts_channel"] == 5
    assert plan["rmialerts"] == 5
    # news categories: scanner + bulletin + launch + whale = 4 (mempool/exploit excluded)
    assert plan["rmicryptonews"] == 4
    assert plan["main_channel"] == 2  # whale critical + launch critical


def test_ingest_and_route_with_custom_thresholds() -> None:
    raw = [("scanner", {"title": "Scan", "risk_score": 50, "token_address": "m1"})]
    result = ingest_and_route(
        raw,
        thresholds={"site": {"min_severity": "medium", "categories": ["scanner"]}},
    )
    plan = {entry["channel"]: entry["count"] for entry in result["plan"]}
    assert plan["site"] == 1
