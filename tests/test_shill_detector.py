"""Dry tests for the KOL shill detector rug-history wiring.

A KOL with documented rug history should be treated as more likely to
shill-dump again: the min-sell alert threshold is lowered, so a smaller
sell after a bullish post still triggers a shill_then_dump alert.
"""

from __future__ import annotations

import json

from app.databus import shill_detector as sd


def _post(author: str = "rugkiller") -> dict:
    return {
        "id": "post-1",
        "author": author,
        "sentiment": "bullish",
        "token_symbol": "RUGCOIN",
        "timestamp": "2026-08-01T12:00:00+00:00",
    }


def _wallet() -> list[dict]:
    return [{"address": "wallet1", "chain": "solana", "confidence": "strong", "status": "active"}]


def _tx(amount_usd: float) -> list[dict]:
    return [
        {
            "wallet": "wallet1",
            "tx_type": "sell",
            "token_symbol": "RUGCOIN",
            "amount_usd": amount_usd,
            "ts": "2026-08-01T13:00:00+00:00",  # 1h after the post
        }
    ]


def _now() -> float:
    import datetime as _dt

    return _dt.datetime(2026, 8, 1, 18, 0, tzinfo=_dt.UTC).timestamp()


def test_no_rug_history_keeps_default_threshold() -> None:
    """$60 sell is below the $100 default threshold → no alert."""
    alerts = sd.detect_shill(_post(), _wallet(), _tx(60.0), now=_now())
    assert alerts == []


def test_rug_history_lowers_sell_threshold() -> None:
    """With documented rug history the $60 sell crosses the halved threshold."""
    alerts = sd.detect_shill(
        _post(),
        _wallet(),
        _tx(60.0),
        kol_rug_history={"rugkiller": [{"token_symbol": "PREVIOUS_RUG", "outcome": "rugged"}]},
        now=_now(),
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["pattern"] == "shill_then_dump"
    assert any(ev.get("kind") == "rug_history" for ev in alert["evidence"])


def test_rug_history_still_respects_floor_threshold() -> None:
    """Tiny sells ($1) stay below the lowered threshold — no alert."""
    alerts = sd.detect_shill(
        _post(),
        _wallet(),
        _tx(1.0),
        kol_rug_history={"rugkiller": [{"token_symbol": "PREVIOUS_RUG"}]},
        now=_now(),
    )
    assert alerts == []


def test_analyze_shill_signals_passes_rug_history() -> None:
    result = sd.analyze_shill_signals(
        {"rugkiller": [_post()]},
        {"rugkiller": _wallet()},
        {"wallet1": _tx(60.0)},
        kol_rug_history={"rugkiller": [{"token_symbol": "PREVIOUS_RUG", "outcome": "rugged"}]},
        now=_now(),
    )
    assert result["total_alerts"] == 1
    assert result["alerts"][0]["pattern"] == "shill_then_dump"


def test_load_kol_rug_history_from_file(tmp_path) -> None:
    p = tmp_path / "kol_rug_history.json"
    p.write_text(
        json.dumps(
            {
                "kol_rug_history": [
                    {
                        "handle": "@KolX",
                        "rugs": [
                            {"token_symbol": "RUGX", "chain": "solana", "outcome": "rugged"},
                            {"token_symbol": "RUGY", "chain": "solana", "outcome": "rugged"},
                        ],
                    },
                    {"handle": "NoRugs", "rugs": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = sd.load_kol_rug_history(str(p))
    assert loaded["kolx"][0]["token_symbol"] == "RUGX"
    assert len(loaded["kolx"]) == 2
    assert loaded["norugs"] == []


def test_load_kol_rug_history_missing_file(tmp_path) -> None:
    loaded = sd.load_kol_rug_history(str(tmp_path / "does_not_exist.json"))
    assert loaded == {}
