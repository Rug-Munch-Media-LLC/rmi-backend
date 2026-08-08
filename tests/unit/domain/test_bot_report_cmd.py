"""Tests for the RugMunchBot /report command (v6 anti-abuse).

``app.domains.telegram.rugmunchbot.handlers.report.cmd_report`` lets users
flag a token address as a scam. Reports are persisted via ``db.report_scam``
and mirrored into the in-memory anti-abuse denylist so the address is blocked
bot-wide immediately.

These tests drive the handler with lightweight fake Telegram objects — no
live bot, no real DB writes, no network.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.telegram.rugmunchbot.handlers import report as report_mod

VALID_EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"


class _FakeUser:
    id = 7075336557
    username = "reporter"
    first_name = "Reporter"


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[tuple[str, dict[str, Any]]] = []

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        self.replies.append((text, kwargs))


class _FakeUpdate:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message
        self.effective_user = _FakeUser()


class _FakeContext:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = list(args or [])
        self.bot = AsyncMock()
        self.bot.send_message = AsyncMock()


def _fresh_module_state() -> None:
    """Reset anti-abuse in-memory denylist mutations between tests."""
    import app.domains.telegram.rugmunchbot.anti_abuse as aa

    aa._ENV_DENYLIST = set()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate DB, owner notify, and anti-abuse module state per test."""
    _fresh_module_state()
    monkeypatch.setattr(report_mod, "OWNER_IDS", {999000111})
    monkeypatch.setattr(report_mod.db, "get_or_create_user", lambda *a, **k: None)


class TestCmdReportUsage:
    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self) -> None:
        """/report with no args prints the usage card and records nothing."""
        msg = _FakeMessage()
        ctx = _FakeContext()
        with patch.object(report_mod.db, "report_scam", new=MagicMock(return_value=True)) as mock_report:
            await report_mod.cmd_report(_FakeUpdate(msg), ctx)

        assert len(msg.replies) == 1
        assert "Report a Scam" in msg.replies[0][0]
        assert "Usage:" in msg.replies[0][0]
        mock_report.assert_not_called()


class TestCmdReportValidation:
    @pytest.mark.asyncio
    async def test_invalid_address_rejected(self) -> None:
        """Non-address args are rejected with an error card."""
        msg = _FakeMessage()
        with patch.object(report_mod.db, "report_scam", new=MagicMock(return_value=True)) as mock_report:
            await report_mod.cmd_report(_FakeUpdate(msg), _FakeContext(["not-an-address"]))

        assert "Invalid address" in msg.replies[0][0]
        mock_report.assert_not_called()

    @pytest.mark.asyncio
    async def test_overlong_address_rejected(self) -> None:
        """Addresses that fail shape validation are rejected, never stored."""
        msg = _FakeMessage()
        long_addr = "0x" + "a" * 70
        with patch.object(report_mod.db, "report_scam", new=MagicMock(return_value=True)) as mock_report:
            await report_mod.cmd_report(_FakeUpdate(msg), _FakeContext([long_addr]))

        assert "Invalid address" in msg.replies[0][0]
        mock_report.assert_not_called()


class TestCmdReportSuccess:
    @pytest.mark.asyncio
    async def test_valid_report_persists_and_mirrors_denylist(self) -> None:
        """A valid EVM address is reported, denylisted, and confirmed."""
        import app.domains.telegram.rugmunchbot.anti_abuse as aa

        msg = _FakeMessage()
        ctx = _FakeContext([VALID_EVM, "honeypot", "can't", "sell"])
        with (
            patch.object(report_mod.db, "report_scam", new=MagicMock(return_value=True)) as mock_report,
            patch.object(aa, "register_reported_scam", new=lambda addr: aa._ENV_DENYLIST.add(addr.lower())) as mock_reg,
        ):
            await report_mod.cmd_report(_FakeUpdate(msg), ctx)

        mock_report.assert_called_once()
        addr_arg = mock_report.call_args.args[1]
        assert addr_arg == VALID_EVM.lower()
        assert mock_report.call_args.args[2] == "ethereum"  # chain
        assert mock_report.call_args.args[3] == "honeypot can't sell"  # reason joined
        # Confirmation card
        assert "Scam report submitted" in msg.replies[0][0]
        # The owner was notified (best effort)
        ctx.bot.send_message.assert_awaited()

    @pytest.mark.asyncio
    async def test_report_address_now_flagged_as_known_scam(self) -> None:
        """After a report, the same address is blocked by is_known_scam_address."""
        import app.domains.telegram.rugmunchbot.anti_abuse as aa

        msg = _FakeMessage()
        ctx = _FakeContext([VALID_EVM])
        await report_mod.cmd_report(_FakeUpdate(msg), ctx)

        # register_reported_scam is the real function here — the in-memory
        # denylist must now contain the address (lowercased).
        assert aa.is_known_scam_address(VALID_EVM) is True

    @pytest.mark.asyncio
    async def test_db_failure_reports_error_card(self) -> None:
        """When the DB write fails, the user sees an error, not a success."""
        msg = _FakeMessage()
        with patch.object(report_mod.db, "report_scam", new=MagicMock(return_value=False)):
            await report_mod.cmd_report(_FakeUpdate(msg), _FakeContext([VALID_EVM]))

        assert "Report failed" in msg.replies[0][0]

    @pytest.mark.asyncio
    async def test_owner_notify_failure_does_not_crash(self) -> None:
        """A failing owner notify must not prevent the confirmation reply."""
        msg = _FakeMessage()
        ctx = _FakeContext([VALID_EVM])
        ctx.bot.send_message.side_effect = RuntimeError("telegram down")

        await report_mod.cmd_report(_FakeUpdate(msg), ctx)

        assert "Scam report submitted" in msg.replies[0][0]
