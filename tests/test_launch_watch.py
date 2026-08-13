"""Pure unit tests for launch-watch collection + rmialpha routing.

Mocks the ``TokenLaunchDetector`` (and the singleton factory) — no network,
no DB. Covers: detector output -> canonical ``PlatformAlert`` field mapping,
fail-open ``[]`` on detector failure, malformed-item skipping, the default
singleton path, and ``route_alerts`` routing a launch alert to ``rmialpha`` +
``rmicryptonews`` (never ``main_channel`` unless critical).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from app.domains.alerts import launch_watch
from app.domains.alerts.models import PlatformAlert
from app.domains.alerts.publisher import DEFAULT_CHANNELS, route_alerts


def _launch_raw(**overrides: Any) -> dict[str, Any]:
    """A raw ``TokenLaunchDetector.get_recent_launches`` dict (real shape)."""
    raw: dict[str, Any] = {
        "address": "TokenMintAddr123456789",
        "chain": "solana",
        "symbol": "RUG",
        "name": "TestCoin",
        "price": 0.0001,
        "liquidity": 12500.0,
        "market_cap": 50000,
        "created_at": 1780000000000,
        "age_seconds": 45,
        "dex": "pump.fun",
        "pair_address": "PairAddrABC",
        "source": "gmgn",
    }
    raw.update(overrides)
    return raw


# ── Collection: detector output -> PlatformAlert mapping ────────────────────


async def test_collect_launch_alerts_maps_detector_result() -> None:
    detector = AsyncMock()
    detector.get_recent_launches.return_value = [
        _launch_raw(),
        _launch_raw(symbol="BONK", address="MintB", chain="base"),
    ]

    alerts = await launch_watch.collect_launch_alerts(detector=detector)

    detector.get_recent_launches.assert_awaited_once_with(limit=50)
    assert len(alerts) == 2

    first = alerts[0]
    assert isinstance(first, PlatformAlert)
    assert first.category == "launch"
    assert first.severity == "high"
    assert first.title == "New launch: RUG (solana)"
    assert first.token_address == "TokenMintAddr123456789"
    assert first.chain == "solana"
    assert first.source == "launch_detector"
    # body carries pair / address / initial liquidity / age
    assert "Pair: PairAddrABC" in first.body
    assert "Address: TokenMintAddr123456789" in first.body
    assert "Initial liquidity: $12,500" in first.body
    assert "Age: 45s" in first.body
    assert "DEX: pump.fun" in first.body

    second = alerts[1]
    assert second.title == "New launch: BONK (base)"
    assert second.token_address == "MintB"
    assert second.chain == "base"


async def test_collect_launch_alerts_returns_empty_on_detector_failure() -> None:
    detector = AsyncMock()
    detector.get_recent_launches.side_effect = RuntimeError("redis down")

    assert await launch_watch.collect_launch_alerts(detector=detector) == []


async def test_collect_launch_alerts_skips_malformed_items() -> None:
    detector = AsyncMock()
    detector.get_recent_launches.return_value = [
        _launch_raw(),
        {},  # no address / symbol -> skipped
        {"address": "x", "symbol": "???"},  # unknown symbol -> skipped
        {"symbol": "NOPE"},  # no address -> skipped
    ]

    alerts = await launch_watch.collect_launch_alerts(detector=detector)

    assert len(alerts) == 1
    assert alerts[0].title == "New launch: RUG (solana)"


async def test_collect_launch_alerts_uses_default_singleton_when_detector_none() -> None:
    detector = AsyncMock()
    detector.get_recent_launches.return_value = [_launch_raw()]
    with patch("app.databus.token_launch_detector.get_launch_detector", return_value=detector) as factory:
        alerts = await launch_watch.collect_launch_alerts()

    factory.assert_called_once_with()
    detector.get_recent_launches.assert_awaited_once_with(limit=50)
    assert len(alerts) == 1
    assert alerts[0].source == "launch_detector"


# ── Routing: launch -> rmialpha + rmicryptonews ─────────────────────────────


def _launch_alert(severity: str = "high") -> PlatformAlert:
    return PlatformAlert(
        severity=severity,
        category="launch",
        title="New launch: RUG (solana)",
        body="Pair: PairAddrABC\nAddress: TokenMintAddr123456789\nInitial liquidity: $1,251\nAge: 45s",
        token_address="TokenMintAddr123456789",
        chain="solana",
        source="launch_detector",
    )


def test_route_launch_alert_to_rmialpha_and_rmicryptonews() -> None:
    plan = route_alerts([_launch_alert()])
    by_channel = {entry["channel"]: entry["count"] for entry in plan}

    assert by_channel["rmialpha"] == 1  # high+ launch lands on alpha
    assert by_channel["rmicryptonews"] == 1  # launch is a news category
    assert by_channel["alerts_channel"] == 1  # high+
    assert by_channel["site"] == 1
    assert by_channel["main_channel"] == 0  # high launch is NOT critical


def test_route_critical_launch_to_main_channel() -> None:
    plan = route_alerts([_launch_alert(severity="critical")])
    by_channel = {entry["channel"]: entry["count"] for entry in plan}

    assert by_channel["rmialpha"] == 1
    assert by_channel["rmicryptonews"] == 1
    assert by_channel["main_channel"] == 1  # critical only


def test_rmialpha_policy_allowlist_and_severity() -> None:
    policy = DEFAULT_CHANNELS["rmialpha"]
    assert policy.min_severity == "high"
    assert policy.categories == {"launch", "syndicate", "whale"}

    launch = _launch_alert()
    syndicate = PlatformAlert(severity="high", category="syndicate", title="S", source="syndicate")
    whale = PlatformAlert(severity="high", category="whale", title="W", source="whale")
    scanner = PlatformAlert(severity="high", category="scanner", title="N", source="scanner")
    medium_launch = PlatformAlert(severity="medium", category="launch", title="M", source="launch_detector")

    plan = route_alerts([launch, syndicate, whale, scanner, medium_launch])
    alpha_titles = {a["title"] for entry in plan if entry["channel"] == "rmialpha" for a in entry["alerts"]}
    # launch/syndicate/whale at high+; scanner excluded (not alpha category),
    # medium launch excluded (below high threshold).
    assert alpha_titles == {"New launch: RUG (solana)", "S", "W"}
