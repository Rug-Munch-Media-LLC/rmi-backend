"""Scan-alert delivery: one target, gated, message id returned.

No network: ``send_alert`` and channel resolution are monkeypatched, so the
test proves the routing decision (which chat, how many targets) rather than
the Bot API itself.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.alerts import scan_delivery

ROW: dict[str, Any] = {
    "alert_id": "alert_a0c37f00d3af9159",
    "chain": "ethereum",
    "alert_type": "wallet_cluster",
    "severity": "critical",
    "title": "Clustered wallets holding supply on ethereum 0x79d0…95c4 (risk 75/100, CRITICAL)",
    "description": "19 wallets funded from 0xbcd577de… within 1h.",
    "token_id": "0x79d00000000000000000000000000000000095c4",
}


@pytest.fixture(autouse=True)
def _one_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan_delivery, "channel_ids_from_env", lambda **_: {"alerts_channel": "@rmialerts"})
    for key in ("RMI_CHANNEL_DRY_RUN", "RMI_CHANNEL_DRY_RUN_ALERTS"):
        monkeypatch.delenv(key, raising=False)


def _sent(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, "results": [{"ok": True, "chat_id": "@rmialerts", "message_id": 4242}]}


@pytest.mark.asyncio
async def test_dry_by_default_never_touches_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []

    async def fake_send(*args: Any, **kwargs: Any) -> dict[str, Any]:
        called.append(args)
        return _sent()

    monkeypatch.setattr(scan_delivery, "send_alert", fake_send)
    result = await scan_delivery.deliver_scan_alert(ROW)

    assert result["channel"] == "alerts_channel"
    assert result["gate"] == "RMI_CHANNEL_DRY_RUN_ALERTS"
    assert result["live"] is False
    assert result["sent"] is False
    assert result["message_id"] is None
    assert called == []


@pytest.mark.asyncio
async def test_live_role_sends_to_exactly_one_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_send(text: str, **kwargs: Any) -> dict[str, Any]:
        seen.append({"text": text, **kwargs})
        return _sent()

    monkeypatch.setattr(scan_delivery, "send_alert", fake_send)
    monkeypatch.setenv("RMI_CHANNEL_DRY_RUN_ALERTS", "0")
    result = await scan_delivery.deliver_scan_alert(ROW)

    assert result["sent"] is True
    assert result["message_id"] == 4242
    assert result["link"] == "https://t.me/rmialerts/4242"
    assert len(seen) == 1, seen
    assert seen[0]["chat_id"] == "@rmialerts"
    assert "CRITICAL" in seen[0]["text"]


@pytest.mark.asyncio
async def test_global_force_dry_beats_the_role_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RMI_CHANNEL_DRY_RUN_ALERTS", "0")
    monkeypatch.setenv("RMI_CHANNEL_DRY_RUN", "1")
    result = await scan_delivery.deliver_scan_alert(ROW)
    assert result["live"] is False
    assert result["sent"] is False
