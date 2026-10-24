"""Security + wallet normalization: flags, nulls-with-reason, no signed calls."""

from __future__ import annotations

from app.domains.gmgn_intel.security import normalize_security
from app.domains.gmgn_intel.wallet import build_wallet_profile

OBSERVED = "2026-10-24T12:00:00+00:00"


def test_security_flags_and_fields() -> None:
    sec = normalize_security(
        {
            "is_honeypot": True,
            "can_not_sell": True,
            "rug_ratio": 0.7,
            "top_10_holder_rate": 0.62,
            "buy_tax": 0.05,
            "sell_tax": 18,
            "sniper_count": 12,
            "burn_status": "burn",
        },
        OBSERVED,
    )
    assert sec["is_honeypot"] is True
    assert sec["sniper_count"] == 12
    assert {"honeypot", "cannot_sell", "top10_concentration", "high_rug_ratio", "high_tax"} <= set(sec["flags"])
    assert sec["source"] == "GET /v1/token/security"


def test_security_absent_reports_reason() -> None:
    sec = normalize_security(None, OBSERVED)
    assert sec["is_honeypot"] is False
    assert sec["rug_ratio"] is None
    assert sec["missing"][0]["reason"] == "endpoint returned no data"


def test_wallet_profile_normalizes_stats_and_activity() -> None:
    detail = {
        "wallet": "W1",
        "source": "gmgn",
        "realized_profit": "1234.5",
        "winrate": 0.61,
        "token_num": "42",
        "buy": 80,
        "sell": 35,
        "tags": ["smart_money"],
        "activities": [
            {
                "token_address": "T1",
                "token_symbol": "AAA",
                "side": "buy",
                "amount_usd": "120.5",
                "timestamp": 1792828021,
            }
        ],
    }
    profile = build_wallet_profile(detail, "W1", "sol", "7d", OBSERVED)
    assert profile["chain"] == "solana"
    assert profile["realized_profit_usd"] == 1234.5
    assert profile["tokens_traded"] == 42
    assert profile["trades"] == {"buy": 80, "sell": 35}
    assert profile["activity"][0]["token_symbol"] == "AAA"
    assert profile["activity"][0]["timestamp"].startswith("2026-")
    assert profile["holdings"] is None
    assert any(item["field"] == "holdings" for item in profile["missing"])


def test_wallet_profile_without_upstream_data_lists_gaps() -> None:
    profile = build_wallet_profile({"wallet": "W2", "source": "none"}, "W2", "solana", "7d", OBSERVED)
    gaps = {item["field"] for item in profile["missing"]}
    assert {"wallet", "winrate", "activity", "holdings"} <= gaps
    assert profile["winrate"] is None
