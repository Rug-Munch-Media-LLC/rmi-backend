"""Tests for the Stars-paid token Monitor feature.

Covers the four moving parts added for monetized monitoring:

  1. ``db.monitors`` — SQLite CRUD (add/get/remove/expire/update_baseline).
  2. ``handlers.alerts`` — security baseline extraction + delta detection
     (tax / ownership / mint / LP changes) and the ``check_monitors`` job
     that enqueues meaningful changes onto the existing alert queue.
  3. ``handlers.payments`` — precheckout accepts the ``mon_`` payload prefix.
  4. Keyboard/copy — the pricing surface points at working Stars features
     (Monitor 250★ / Deep Scan 250★), never fake $/mo tiers.

No network, no Telegram API, no real Redis — DB is monkeypatched to a tmp
path and the scanner/enqueue are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.alerts import (
    MONITOR_EVENT_TYPES,
    _monitor_baseline,
    _monitor_deltas,
)


@pytest.fixture(autouse=True)
def _tmp_db_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "monitor_test.db"))
    # Table creation runs at module import against the real DB path, so
    # re-init against the patched path (idempotent CREATE IF NOT EXISTS).
    db.init_db()


# ── db.monitors CRUD ─────────────────────────────────────────────────────


def test_add_get_monitor_roundtrip() -> None:
    assert db.add_monitor(1, "0xabc", "ethereum", symbol="ABC", baseline={"buy_tax": 0.05})
    rows = db.get_monitors(1)
    assert len(rows) == 1
    assert rows[0]["token_address"] == "0xabc"
    assert rows[0]["baseline"]["buy_tax"] == 0.05
    # Duplicate (same user + token) is rejected.
    assert not db.add_monitor(1, "0xabc", "ethereum")
    # Different user is allowed.
    assert db.add_monitor(2, "0xabc", "ethereum")


def test_remove_monitor_deactivates() -> None:
    db.add_monitor(1, "0xabc", "ethereum")
    assert db.remove_monitor(1, "0xABC")  # case-insensitive
    assert db.get_monitors(1) == []


def test_expire_monitors_deactivates_past_window() -> None:
    db.add_monitor(1, "0xabc", "ethereum")
    db.add_monitor(2, "0xdef", "ethereum")
    # Force-expire the first monitor by rewriting its expires_at.
    conn = db.get_db()
    conn.execute("UPDATE monitors SET expires_at = ? WHERE user_id = 1", (0,))
    conn.commit()
    conn.close()
    assert db.expire_monitors() == 1
    active = db.get_active_monitors()
    assert len(active) == 1
    assert active[0]["user_id"] == 2


def test_update_monitor_baseline() -> None:
    db.add_monitor(1, "0xabc", "ethereum")
    mon = db.get_monitors(1)[0]
    db.update_monitor_baseline(mon["id"], {"buy_tax": 0.2})
    assert db.get_monitors(1)[0]["baseline"]["buy_tax"] == 0.2


# ── Baseline + delta detection ───────────────────────────────────────────


def test_monitor_baseline_projects_security_fields() -> None:
    baseline = _monitor_baseline(
        {
            "buy_tax": 0.05,
            "sell_tax": 0.09,
            "owner_renounced": False,
            "mintable": False,
            "lp_locked": 0.8,
            "honeypot": False,
        }
    )
    assert baseline == {
        "buy_tax": 0.05,
        "sell_tax": 0.09,
        "owner_renounced": False,
        "mintable": False,
        "lp_locked": 0.8,
        "honeypot": False,
    }


def test_monitor_deltas_no_change() -> None:
    base = {
        "buy_tax": 0.05,
        "sell_tax": 0.09,
        "owner_renounced": False,
        "mintable": False,
        "lp_locked": 0.8,
        "honeypot": False,
    }
    assert _monitor_deltas(base, dict(base)) == []


def test_monitor_deltas_detects_tax_and_ownership_changes() -> None:
    base = {
        "buy_tax": 0.05,
        "sell_tax": 0.09,
        "owner_renounced": False,
        "mintable": False,
        "lp_locked": 0.8,
        "honeypot": False,
    }
    current = dict(base)
    current["buy_tax"] = 0.2  # +15pp — rug setup
    current["owner_renounced"] = False  # unchanged
    current["mintable"] = True  # mint authority added
    changes = _monitor_deltas(base, current)
    assert any("Buy Tax" in c for c in changes)
    assert any("Mint authority" in c for c in changes)
    assert not any("Ownership" in c for c in changes)


def test_monitor_deltas_detects_lp_unlock() -> None:
    base = {
        "buy_tax": None,
        "sell_tax": None,
        "owner_renounced": None,
        "mintable": None,
        "lp_locked": 0.8,
        "honeypot": None,
    }
    current = dict(base)
    current["lp_locked"] = 0.1  # -70pp
    changes = _monitor_deltas(base, current)
    assert any("LP locked" in c for c in changes)


def test_monitor_deltas_ignores_small_tax_move() -> None:
    base = {
        "buy_tax": 0.05,
        "sell_tax": 0.09,
        "owner_renounced": False,
        "mintable": False,
        "lp_locked": 0.8,
        "honeypot": False,
    }
    current = dict(base)
    current["buy_tax"] = 0.055  # +0.5pp — below the 1pp threshold
    assert _monitor_deltas(base, current) == []


# ── check_monitors background job ────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_monitors_enqueues_dm_on_delta() -> None:
    db.add_monitor(
        7,
        "0xabc",
        "ethereum",
        symbol="ABC",
        baseline={
            "buy_tax": 0.05,
            "sell_tax": 0.09,
            "owner_renounced": False,
            "mintable": False,
            "lp_locked": 0.8,
            "honeypot": False,
        },
    )
    scan_result = {
        "address": "0xabc",
        "chain": "ethereum",
        "symbol": "ABC",
        "buy_tax": 0.25,
        "sell_tax": 0.09,
        "owner_renounced": False,
        "mintable": False,
        "lp_locked": 0.8,
        "honeypot": False,
    }
    enqueued: list[dict] = []

    async def _fake_enqueue(user_id, event_type, payload, event_id):
        enqueued.append({"user_id": user_id, "type": event_type, "payload": payload})
        return True

    ctx = AsyncMock()
    with (
        patch("app.domains.scanner.webhooks.telegram_bridge.enqueue_telegram_alert", side_effect=_fake_enqueue),
        patch("app.domains.telegram.rugmunchbot.scanner.scan_token", AsyncMock(return_value=scan_result)),
    ):
        from app.domains.telegram.rugmunchbot.handlers.alerts import check_monitors

        await check_monitors(ctx)

    assert len(enqueued) == 1
    assert enqueued[0]["user_id"] == 7
    assert enqueued[0]["type"] == "token.monitor_change"
    assert any("Buy Tax" in c for c in enqueued[0]["payload"]["changes"])


@pytest.mark.asyncio
async def test_check_monitors_skips_silent_when_no_delta() -> None:
    base = {
        "buy_tax": 0.05,
        "sell_tax": 0.09,
        "owner_renounced": False,
        "mintable": False,
        "lp_locked": 0.8,
        "honeypot": False,
    }
    db.add_monitor(7, "0xabc", "ethereum", symbol="ABC", baseline=base)
    enqueued: list[dict] = []

    async def _fake_enqueue(user_id, event_type, payload, event_id):
        enqueued.append({"user_id": user_id, "type": event_type})
        return True

    ctx = AsyncMock()
    with (
        patch("app.domains.scanner.webhooks.telegram_bridge.enqueue_telegram_alert", side_effect=_fake_enqueue),
        patch(
            "app.domains.telegram.rugmunchbot.scanner.scan_token",
            AsyncMock(return_value=dict(base, address="0xabc", chain="ethereum")),
        ),
    ):
        from app.domains.telegram.rugmunchbot.handlers.alerts import check_monitors

        await check_monitors(ctx)

    assert enqueued == []


# ── Payment wiring ───────────────────────────────────────────────────────


def test_monitor_event_types_covers_rug_signals() -> None:
    assert "token.liquidity_change" in MONITOR_EVENT_TYPES
    assert "token.whale_move" in MONITOR_EVENT_TYPES
    assert "token.price_change" in MONITOR_EVENT_TYPES


def test_precheckout_accepts_mon_payload() -> None:
    from app.domains.telegram.rugmunchbot.handlers.payments import precheckout

    update = AsyncMock()
    update.pre_checkout_query = AsyncMock()
    update.pre_checkout_query.invoice_payload = "mon_abc123"
    ctx = AsyncMock()

    async def _run() -> None:
        await precheckout(update, ctx)

    import asyncio

    asyncio.run(_run())
    update.pre_checkout_query.answer.assert_awaited_once_with(ok=True)


def test_pricing_kb_has_working_stars_features() -> None:
    from app.domains.telegram.rugmunchbot.keyboards import pricing_kb

    markup = pricing_kb()
    flat = [b.text for row in markup.inline_keyboard for b in row]
    assert any("Monitor (250" in t for t in flat)
    assert any("Buy Stars" in t for t in flat)
    assert not any("$/mo" in t for t in flat)


# ── Lighter delta path: security_only refresh + per-tick dedup ────────────


@pytest.mark.asyncio
async def test_check_monitors_uses_security_only_refresh() -> None:
    """The delta job must use the cheap security-only scan path, not a full
    market+enrichment scan."""
    db.add_monitor(
        7,
        "0xabc",
        "ethereum",
        symbol="ABC",
        baseline={
            "buy_tax": 0.05,
            "sell_tax": 0.09,
            "owner_renounced": False,
            "mintable": False,
            "lp_locked": 0.8,
            "honeypot": False,
        },
    )
    scan_token_mock = AsyncMock(return_value={"address": "0xabc", "chain": "ethereum"})
    ctx = AsyncMock()
    with (
        patch("app.domains.scanner.webhooks.telegram_bridge.enqueue_telegram_alert", AsyncMock(return_value=True)),
        patch("app.domains.telegram.rugmunchbot.scanner.scan_token", scan_token_mock),
    ):
        from app.domains.telegram.rugmunchbot.handlers.alerts import check_monitors

        await check_monitors(ctx)

    scan_token_mock.assert_awaited_once_with("0xabc", "ethereum", security_only=True)


@pytest.mark.asyncio
async def test_check_monitors_dedupes_same_token_per_tick() -> None:
    """Two users monitoring the SAME token share ONE security-only refresh."""
    base = {
        "buy_tax": 0.05,
        "sell_tax": 0.09,
        "owner_renounced": False,
        "mintable": False,
        "lp_locked": 0.8,
        "honeypot": False,
    }
    db.add_monitor(7, "0xabc", "ethereum", symbol="ABC", baseline=base)
    db.add_monitor(8, "0xabc", "ethereum", symbol="ABC", baseline=base)
    scan_token_mock = AsyncMock(
        return_value={"address": "0xabc", "chain": "ethereum", "buy_tax": 0.05, "sell_tax": 0.09}
    )
    ctx = AsyncMock()
    with (
        patch("app.domains.scanner.webhooks.telegram_bridge.enqueue_telegram_alert", AsyncMock(return_value=True)),
        patch("app.domains.telegram.rugmunchbot.scanner.scan_token", scan_token_mock),
    ):
        from app.domains.telegram.rugmunchbot.handlers.alerts import check_monitors

        await check_monitors(ctx)

    scan_token_mock.assert_awaited_once()  # one refresh, not two


# ── /monitor → invoice → successful payment → baseline → subscribe → DM ───


def test_cmd_monitor_sends_stars_invoice() -> None:
    """/monitor sends a Telegram Stars invoice (XTR, mon_ payload) before any
    subscription is registered."""
    from app.domains.telegram.rugmunchbot.handlers.watchlist import MONITOR_STARS, cmd_monitor

    update = AsyncMock()
    user = AsyncMock()
    user.id = 42
    user.username = "tester"
    user.first_name = "T"
    update.effective_user = user
    update.message.chat.id = 42
    ctx = AsyncMock()
    ctx.args = ["0x6982508145454Ce325dDbE47a25d4ec3d2311933"]

    import asyncio

    asyncio.run(cmd_monitor(update, ctx))

    ctx.bot.send_invoice.assert_awaited_once()
    _, kwargs = ctx.bot.send_invoice.await_args
    assert kwargs["payload"].startswith("mon_")
    assert kwargs["currency"] == "XTR"
    assert kwargs["prices"][0].amount == MONITOR_STARS
    # No monitor row until payment clears.
    assert db.get_monitors(42) == []


@pytest.mark.asyncio
async def test_successful_payment_mon_flow_baseline_subscribe_dm() -> None:
    """Stars payment for a mon_ payload runs: baseline scan → db row → backend
    subscription → "Monitor Active" DM. The delta job then DMs on change."""
    from app.domains.telegram.rugmunchbot.handlers.payments import successful_payment

    token = "tokabc123"
    from app.domains.telegram.rugmunchbot.states import _pending_monitors

    _pending_monitors[token] = {
        "user_id": 42,
        "address": "0xabc",
        "chain": "ethereum",
    }

    scan_result = {
        "address": "0xabc",
        "chain": "ethereum",
        "symbol": "ABC",
        "buy_tax": 0.05,
        "sell_tax": 0.09,
        "owner_renounced": False,
        "mintable": False,
        "lp_locked": 0.8,
        "honeypot": False,
        "deployer_address": "0xdeployer",
    }

    update = AsyncMock()
    user = AsyncMock()
    user.id = 42
    user.username = "tester"
    user.first_name = "T"
    update.effective_user = user
    payment = AsyncMock()
    payment.invoice_payload = f"mon_{token}"
    payment.telegram_payment_charge_id = "chg_1"
    payment.total_amount = 250
    update.message.successful_payment = payment
    edit_mock = AsyncMock()
    update.message.reply_text.return_value = edit_mock

    subscribed: list[tuple[int, str]] = []

    async def _fake_subscribe(user_id, addr):
        subscribed.append((user_id, addr))
        return True

    with (
        patch("app.domains.telegram.rugmunchbot.handlers.payments.scan_token", AsyncMock(return_value=scan_result)),
        patch(
            "app.domains.telegram.rugmunchbot.handlers.alerts._subscribe_backend_monitor",
            side_effect=_fake_subscribe,
        ),
    ):
        await successful_payment(update, AsyncMock())

    rows = db.get_monitors(42)
    assert len(rows) == 1
    assert rows[0]["token_address"] == "0xabc"
    assert rows[0]["baseline"]["buy_tax"] == 0.05
    assert subscribed == [(42, "0xabc")]
    edit_mock.edit_text.assert_awaited_once()
    assert "Monitor Active" in edit_mock.edit_text.await_args[0][0]
    # Delta job DMs the registered user on a real change.
    enqueued: list[dict] = []
    ctx = AsyncMock()

    async def _fake_enqueue(user_id, event_type, payload, event_id):
        enqueued.append({"user_id": user_id, "type": event_type, "payload": payload})
        return True

    from app.domains.telegram.rugmunchbot.handlers.alerts import check_monitors

    changed = dict(scan_result, buy_tax=0.25)
    with (
        patch("app.domains.scanner.webhooks.telegram_bridge.enqueue_telegram_alert", side_effect=_fake_enqueue),
        patch("app.domains.telegram.rugmunchbot.scanner.scan_token", AsyncMock(return_value=changed)),
    ):
        await check_monitors(ctx)

    assert any(e["user_id"] == 42 and e["type"] == "token.monitor_change" for e in enqueued)
    _pending_monitors.pop(token, None)
