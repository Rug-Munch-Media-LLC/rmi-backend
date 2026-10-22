"""Per-channel live gate: dry by default, one channel at a time, global dries.

No network. The env is set and restored around each test with plain
``os.environ`` so the gate's real lookup path is exercised.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.channel_gate import assert_single_live, gate_line, is_live, live_roles

ROLES = ("news", "scans", "alpha", "alerts", "main")
_KEYS = ("RMI_CHANNEL_DRY_RUN", *(f"RMI_CHANNEL_DRY_RUN_{r.upper()}" for r in ROLES))


@pytest.fixture(autouse=True)
def _clean_env() -> Any:
    saved = {k: os.environ.get(k) for k in _KEYS}
    for k in _KEYS:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_every_channel_is_dry_by_default() -> None:
    assert [is_live(role) for role in ROLES] == [False] * len(ROLES)


def test_one_named_channel_goes_live_alone() -> None:
    os.environ["RMI_CHANNEL_DRY_RUN_NEWS"] = "0"
    assert is_live("news") is True
    assert live_roles(ROLES) == ("news",)
    assert assert_single_live(ROLES) is None


def test_any_value_other_than_zero_stays_dry() -> None:
    for value in ("1", "off", "no", "", "true"):
        os.environ["RMI_CHANNEL_DRY_RUN_NEWS"] = value
        assert is_live("news") is False


def test_global_zero_never_enables_anything() -> None:
    os.environ["RMI_CHANNEL_DRY_RUN"] = "0"
    assert live_roles(ROLES) == ()


def test_global_force_dry_overrides_a_live_channel() -> None:
    os.environ["RMI_CHANNEL_DRY_RUN"] = "1"
    os.environ["RMI_CHANNEL_DRY_RUN_NEWS"] = "0"
    assert is_live("news") is False


def test_two_live_channels_is_a_violation() -> None:
    os.environ["RMI_CHANNEL_DRY_RUN_NEWS"] = "0"
    os.environ["RMI_CHANNEL_DRY_RUN_SCANS"] = "0"
    violation = assert_single_live(ROLES)
    assert violation is not None
    assert "news" in violation
    assert "scans" in violation


def test_gate_line_names_the_variable() -> None:
    assert gate_line("scans") == "scans:DRY [RMI_CHANNEL_DRY_RUN_SCANS]"


@pytest.mark.asyncio
async def test_publish_routed_never_sends_while_dry() -> None:
    """A dry news entry is skipped before the transport is touched."""
    from app.domains.alerts import telegram_transport as transport
    from app.domains.alerts.models import PlatformAlert

    client = AsyncMock()
    client.send_message = AsyncMock(return_value=True)
    plan = [
        {
            "channel": "rmicryptonews",
            "alerts": [
                PlatformAlert(
                    severity="low",
                    category="bulletin",
                    title="Exchange lists new asset",
                    body="Body",
                    token_address="0xABC",
                    chain="ethereum",
                    source="news",
                ).to_dict()
            ],
        }
    ]
    result = await transport.publish_routed(plan, client=client)
    assert result["sent"] == 0
    assert result["skipped"] == 1
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_routed_sends_when_that_one_role_is_live() -> None:
    from app.domains.alerts import telegram_transport as transport
    from app.domains.alerts.models import PlatformAlert

    os.environ["RMI_CHANNEL_DRY_RUN_NEWS"] = "0"
    client = AsyncMock()
    client.send_message = AsyncMock(return_value=True)
    plan = [
        {
            "channel": "rmicryptonews",
            "alerts": [
                PlatformAlert(
                    severity="low",
                    category="bulletin",
                    title="Exchange lists new asset",
                    body="Body",
                    token_address="0xABC",
                    chain="ethereum",
                    source="news",
                ).to_dict()
            ],
        }
    ]
    result = await transport.publish_routed(plan, client=client)
    assert result["sent"] == 1, result
    client.send_message.assert_awaited_once()
