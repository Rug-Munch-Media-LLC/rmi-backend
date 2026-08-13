"""Pure unit tests for the whale-watch collector + bot channels section.

Covers:
  - ``collect_whale_alerts`` maps WhaleAlert-shaped moves (dicts and the
    ``WhaleAlert`` dataclass) into canonical ``PlatformAlert`` objects with
    category ``whale``, severity ``high``, source ``whale_watcher`` and the
    correct token/title/body fields;
  - fail-open: a raising watcher, a default watcher with no data, and
    malformed items never raise and yield ``[]`` / drop the item;
  - routing: a collected whale alert lands on the ``rmialpha`` channel via
    ``publisher.route_alerts`` (whale is in the rmialpha allowlist);
  - the collector's raw shape is also accepted by ``adapt_whale``;
  - the /help text renders a Channels section from the real
    ``config.CHANNELS`` values.

No network, no DB.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("ENVIRONMENT", "dev")

from app.domains.alerts.adapters import adapt_whale
from app.domains.alerts.models import PlatformAlert
from app.domains.alerts.publisher import route_alerts
from app.domains.alerts.whale_watch import collect_whale_alerts
from app.domains.telegram.rugmunchbot.config import CHANNELS, TELEGRAM_CHANNEL
from app.domains.telegram.rugmunchbot.handlers.scanning import CHANNEL_LABELS, HELP_TEXT
from app.helius_tools.helius_whale_watcher import WhaleAlert

_TS = "2026-08-13T10:00:00+00:00"


def _whale_move(**overrides: Any) -> dict[str, Any]:
    """A WhaleAlert-shaped raw move (helius WhaleAlert field names)."""
    move: dict[str, Any] = {
        "timestamp": _TS,
        "alert_type": "whale_dump",
        "token_address": "So11111111111111111111111111111111111111112",
        "token_symbol": "SOL",
        "from_wallet": "fromWallet",
        "to_wallet": "toWallet",
        "amount_tokens": 5000.0,
        "amount_usd": 250000.0,
        "pct_of_supply": 3.2,
        "severity": 5,
        "tx_signature": "sig123",
        "whale_tags": ["accumulator"],
    }
    move.update(overrides)
    return move


class _MockWatcher:
    """Minimal injectable watcher exposing a recent-alerts list."""

    def __init__(self, moves: list[Any]) -> None:
        self.recent_alerts = moves


class _RaisingWatcher:
    """Watcher whose recent-alerts accessor blows up (fail-open case)."""

    @property
    def recent_alerts(self) -> list[Any]:
        raise RuntimeError("watcher down")


# ── collect_whale_alerts: mapping ──────────────────────────────────────────


def test_collect_whale_alerts_maps_moves_into_platform_alerts() -> None:
    watcher = _MockWatcher([_whale_move(), _whale_move(token_address="mint2", token_symbol="PEPE")])
    alerts = collect_whale_alerts(watcher)
    assert len(alerts) == 2
    for alert in alerts:
        assert isinstance(alert, PlatformAlert)
        assert alert.category == "whale"
        assert alert.severity == "high"
        assert alert.source == "whale_watcher"
        assert alert.chain == "solana"
        assert alert.token_address
        assert alert.title.startswith("Whale: ")
        assert alert.title.endswith(" moved")


def test_collect_whale_alerts_title_and_body_shape() -> None:
    alerts = collect_whale_alerts(_MockWatcher([_whale_move()]))
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.title == "Whale: 5,000 SOL moved"
    assert alert.token_address == "So11111111111111111111111111111111111111112"
    assert "From: fromWallet" in alert.body
    assert "To: toWallet" in alert.body
    assert "Amount: 5,000 SOL" in alert.body
    assert "USD value: $250,000" in alert.body
    assert "Tx: sig123" in alert.body


def test_collect_whale_alerts_accepts_whale_alert_dataclass_items() -> None:
    move = WhaleAlert(
        timestamp=datetime(2026, 8, 13, 10, 0, tzinfo=UTC),
        alert_type="accumulation",
        token_address="mintW",
        token_symbol="RUG",
        from_wallet="whaleA",
        to_wallet="whaleB",
        amount_tokens=1234.0,
        amount_usd=50000.0,
        pct_of_supply=2.5,
        severity=4,
        tx_signature="sigW",
    )
    alerts = collect_whale_alerts(_MockWatcher([move]))
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.category == "whale"
    assert alert.severity == "high"
    assert alert.title == "Whale: 1,234 RUG moved"
    assert alert.token_address == "mintW"
    assert alert.source == "whale_watcher"


# ── collect_whale_alerts: fail-open ────────────────────────────────────────


def test_collect_whale_alerts_returns_empty_on_watcher_failure() -> None:
    assert collect_whale_alerts(_RaisingWatcher()) == []


def test_collect_whale_alerts_default_watcher_no_network() -> None:
    # No watcher injected -> real helius WhaleWatcher, whose alert history is
    # empty -> [] (no network, no DB).
    assert collect_whale_alerts(None) == []


def test_collect_whale_alerts_skips_malformed_items() -> None:
    watcher = _MockWatcher([_whale_move(), {}, {"token_address": ""}, {"token_address": "   "}])
    alerts = collect_whale_alerts(watcher)
    assert len(alerts) == 1
    assert alerts[0].token_address == "So11111111111111111111111111111111111111112"


# ── Routing: whale -> rmialpha ─────────────────────────────────────────────


def test_route_alerts_sends_whale_alert_to_rmialpha() -> None:
    alerts = collect_whale_alerts(_MockWatcher([_whale_move()]))
    assert alerts
    plan = route_alerts(alerts)
    by_channel = {entry["channel"]: entry for entry in plan}
    alpha = by_channel["rmialpha"]
    assert alpha["count"] == 1
    assert alpha["alerts"][0]["category"] == "whale"
    assert alpha["alerts"][0]["severity"] == "high"
    assert alpha["alerts"][0]["source"] == "whale_watcher"


def test_adapt_whale_accepts_collector_shape() -> None:
    # The raw WhaleAlert shape the collector consumes also normalizes
    # through the canonical adapter (ingestion._norm_whale).
    alert = adapt_whale(_whale_move())
    assert alert is not None
    assert alert.category == "whale"
    assert alert.severity == "critical"  # int severity 5 -> critical via adapter
    assert alert.token_address == "So11111111111111111111111111111111111111112"


# ── /help Channels section ─────────────────────────────────────────────────


def test_help_text_renders_channels_section_from_config() -> None:
    assert "<b>Channels</b>" in HELP_TEXT
    # Every config.CHANNELS channel is listed under its display label.
    for key in CHANNELS:
        assert CHANNEL_LABELS.get(key, key.capitalize()) in HELP_TEXT, f"channel {key!r} missing from /help"
    # Alerts link is the verified public handle from config.
    assert f'<a href="{TELEGRAM_CHANNEL}">Alerts</a>' in HELP_TEXT
    # Alpha is the @rmialpha public handle.
    assert '<a href="https://t.me/rmialpha">Alpha</a>' in HELP_TEXT
    # News link is derived from the real numeric chat id in config.
    news_id = str(CHANNELS["news"])
    assert news_id.startswith("-100")
    assert f"https://t.me/c/{news_id[4:]}" in HELP_TEXT


def test_help_channels_section_html_safe() -> None:
    # No unescaped angle brackets in the rendered section.
    section_start = HELP_TEXT.index("<b>Channels</b>")
    section = HELP_TEXT[section_start:]
    assert "<b>Channels</b>" in section
    # Every anchor is closed (Telegram HTML parse mode requires balanced tags).
    assert section.count("<a ") == section.count("</a>")
    assert "<script" not in section.lower()
