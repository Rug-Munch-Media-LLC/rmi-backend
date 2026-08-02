"""Unit tests for new_token_radar — C3 new token discovery + early warning.

Covers ``scan_new_tokens()`` with a mocked ClickHouse ``deployer_events``
source:

  - Scoring thresholds (CRITICAL / HIGH / WARNING / INFO)
  - Telegram alert triggering (mocked alert_hooks)
  - Redis alert dedup (24h window)
  - Empty deployer_events (no new tokens)
  - F0 graceful fallback (pair_address vs token_address)
  - Result-cache fast path

All external dependencies (ClickHouse, Redis, Telegram, scanner sub-checks)
are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.new_token_radar import (
    _score_new_token,
    clear_cache,
    scan_new_tokens,
)

_TOKEN = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HONEYPOT_TOKEN = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_BENIGN_TOKEN = "0xcccccccccccccccccccccccccccccccccccccccc"
_DEPLOYER = "0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddead"


class FakeCHClient:
    """Sync ClickHouse stand-in returning deployer_events rows."""

    def __init__(self, deployers: list[tuple] | None = None) -> None:
        self.deployers = deployers or []
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if "wallet_memory.deployer_events" in sql:
            return self.deployers
        return []


def _mock_ch_radar(fake: FakeCHClient):
    return patch("app.domains.scanner.engines.new_token_radar._get_ch_client", return_value=fake)


def _no_redis_get():
    return patch("app.domains.scanner.engines.new_token_radar._redis_get", AsyncMock(return_value=None))


def _no_redis_set():
    return patch("app.domains.scanner.engines.new_token_radar._redis_set", AsyncMock(return_value=True))


def _benign_fingerprint():
    """Deployer fingerprint returning a clean bucket (no rug signals)."""
    return patch(
        "app.domains.scanner.engines.deployer_fingerprint.fingerprint_deployer",
        AsyncMock(
            return_value={
                "result": "scanned",
                "risk_bucket": "REPEAT_CLEAN",
                "risk_score": 15,
                "total_deployments": 2,
            }
        ),
    )


def _benign_honeypot():
    """Honeypot check returning a clean scan for every token."""
    return patch(
        "app.domains.scanner.modules.security._check_honeypot_is",
        AsyncMock(return_value={"result": "scanned", "is_honeypot": False, "risk_score": 5}),
    )


def _good_liquidity():
    """Liquidity check returning a healthy USD value."""
    return patch(
        "app.domains.scanner.engines.new_token_radar._check_liquidity",
        AsyncMock(return_value=100_000.0),
    )


def _deployment_row(token: str, ts: str = "2026-08-02 00:00:00") -> tuple:
    return (_DEPLOYER, token, "ethereum", 1, ts, "0xabc", "token", None)


class TestScanNewTokens:
    """Integration tests for scan_new_tokens against a fake ClickHouse."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_empty_deployer_events_returns_empty(self) -> None:
        """No new deployments in the window -> empty result, no crash."""
        fake = FakeCHClient(deployers=[])
        with (
            _mock_ch_radar(fake),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await scan_new_tokens("ethereum", lookback_minutes=5, notify=False)
        assert out == []

    @pytest.mark.asyncio
    async def test_scans_and_scores_benign_token_info(self) -> None:
        """A clean token scores INFO with a low score and full metadata."""
        fake = FakeCHClient(deployers=[_deployment_row(_TOKEN)])
        with (
            _mock_ch_radar(fake),
            _no_redis_get(),
            _no_redis_set(),
            _benign_fingerprint(),
            _benign_honeypot(),
            _good_liquidity(),
        ):
            out = await scan_new_tokens("ethereum", lookback_minutes=5, notify=False)

        assert len(out) == 1
        item = out[0]
        assert item["token_address"] == _TOKEN
        assert item["level"] == "INFO"
        assert item["score"] == 7  # min(39, 15 // 2)
        assert item["signals"]["is_honeypot"] is False
        assert item["signals"]["deployer_bucket"] == "REPEAT_CLEAN"
        assert item["deployer"] == _DEPLOYER

    @pytest.mark.asyncio
    async def test_sorts_critical_before_info(self) -> None:
        """Results are sorted CRITICAL first, then by score descending."""
        fake = FakeCHClient(
            deployers=[
                _deployment_row(_BENIGN_TOKEN, ts="2026-08-02 00:00:02"),
                _deployment_row(_HONEYPOT_TOKEN, ts="2026-08-02 00:00:00"),
            ]
        )
        honeypot_side = AsyncMock(
            side_effect=lambda addr, chain: {
                "result": "scanned",
                "is_honeypot": addr == _HONEYPOT_TOKEN,
                "risk_score": 95 if addr == _HONEYPOT_TOKEN else 5,
            }
        )
        with (
            _mock_ch_radar(fake),
            _no_redis_get(),
            _no_redis_set(),
            _benign_fingerprint(),
            patch("app.domains.scanner.modules.security._check_honeypot_is", honeypot_side),
            _good_liquidity(),
        ):
            out = await scan_new_tokens("ethereum", lookback_minutes=5, notify=False)

        assert [r["level"] for r in out] == ["CRITICAL", "INFO"]
        assert out[0]["token_address"] == _HONEYPOT_TOKEN
        assert out[1]["token_address"] == _BENIGN_TOKEN

    @pytest.mark.asyncio
    async def test_critical_alerts_telegram_with_threats(self) -> None:
        """CRITICAL tokens trigger send_scanner_alert with threat reasons."""
        fake = FakeCHClient(deployers=[_deployment_row(_TOKEN)])
        honeypot_side = AsyncMock(return_value={"result": "scanned", "is_honeypot": True, "risk_score": 95})
        with (
            _mock_ch_radar(fake),
            _no_redis_get(),
            _no_redis_set(),
            _benign_fingerprint(),
            patch("app.domains.scanner.modules.security._check_honeypot_is", honeypot_side),
            patch(
                "app.domains.scanner.engines.new_token_radar._check_liquidity",
                AsyncMock(return_value=100.0),  # low liquidity adds a threat
            ),
            patch(
                "app.domains.scanner.engines.new_token_radar._was_alerted",
                AsyncMock(return_value=False),
            ),
            patch(
                "app.domains.scanner.engines.new_token_radar._mark_alerted",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.domains.telegram.alert_hooks.send_scanner_alert",
                AsyncMock(return_value={"ok": True}),
            ) as send,
        ):
            out = await scan_new_tokens("ethereum", lookback_minutes=5, notify=True)

        assert out[0]["level"] == "CRITICAL"
        assert out[0]["score"] == 100
        send.assert_awaited_once()
        kwargs = send.await_args.kwargs
        assert kwargs["token_address"] == _TOKEN
        assert kwargs["chain"] == "ethereum"
        assert "honeypot" in kwargs["threats"]
        assert "low_liquidity" in kwargs["threats"]
        # Symbol: no lookup result from the fake CH client -> address fallback
        assert kwargs["token_symbol"] == _TOKEN[:8] + "..."

    @pytest.mark.asyncio
    async def test_alert_uses_looked_up_token_symbol_when_available(self) -> None:
        """A symbol returned by _lookup_token_symbol is passed to Telegram."""
        fake = FakeCHClient(deployers=[_deployment_row(_TOKEN)])
        honeypot_side = AsyncMock(return_value={"result": "scanned", "is_honeypot": True, "risk_score": 95})
        with (
            _mock_ch_radar(fake),
            _no_redis_get(),
            _no_redis_set(),
            _benign_fingerprint(),
            patch("app.domains.scanner.modules.security._check_honeypot_is", honeypot_side),
            _good_liquidity(),
            patch(
                "app.domains.scanner.engines.new_token_radar._was_alerted",
                AsyncMock(return_value=False),
            ),
            patch(
                "app.domains.scanner.engines.new_token_radar._mark_alerted",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.domains.scanner.engines.new_token_radar._lookup_token_symbol",
                AsyncMock(return_value="HNY"),
            ),
            patch(
                "app.domains.telegram.alert_hooks.send_scanner_alert",
                AsyncMock(return_value={"ok": True}),
            ) as send,
        ):
            out = await scan_new_tokens("ethereum", lookback_minutes=5, notify=True)

        assert out[0]["level"] == "CRITICAL"
        send.assert_awaited_once()
        assert send.await_args.kwargs["token_symbol"] == "HNY"

    @pytest.mark.asyncio
    async def test_alert_dedup_skips_already_alerted(self) -> None:
        """A token already alerted (Redis 24h window) is not re-sent."""
        fake = FakeCHClient(deployers=[_deployment_row(_TOKEN)])
        honeypot_side = AsyncMock(return_value={"result": "scanned", "is_honeypot": True, "risk_score": 95})
        with (
            _mock_ch_radar(fake),
            _no_redis_get(),
            _no_redis_set(),
            _benign_fingerprint(),
            patch("app.domains.scanner.modules.security._check_honeypot_is", honeypot_side),
            _good_liquidity(),
            patch(
                "app.domains.scanner.engines.new_token_radar._was_alerted",
                AsyncMock(return_value=True),  # already in dedup set
            ),
            patch(
                "app.domains.scanner.engines.new_token_radar._mark_alerted",
                AsyncMock(return_value=None),
            ) as mark,
            patch(
                "app.domains.telegram.alert_hooks.send_scanner_alert",
                AsyncMock(return_value={"ok": True}),
            ) as send,
        ):
            out = await scan_new_tokens("ethereum", lookback_minutes=5, notify=True)

        assert out[0]["level"] == "CRITICAL"  # still scored
        send.assert_not_awaited()  # but not alerted again
        mark.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_result_cache_skips_clickhouse(self) -> None:
        """A cached radar result list is returned without re-polling."""
        cached = [{"token_address": _TOKEN, "chain": "ethereum", "level": "CRITICAL", "score": 100}]
        with (
            patch("app.domains.scanner.engines.new_token_radar._redis_get", AsyncMock(return_value=cached)),
            patch(
                "app.domains.scanner.engines.new_token_radar._query_new_deployments",
                return_value=[],
            ) as query,
        ):
            out = await scan_new_tokens("ethereum", notify=False)
        assert out == cached
        query.assert_not_called()

    @pytest.mark.asyncio
    async def test_f0_fallback_uses_token_address_not_pair_address(self) -> None:
        """Pre-F0 the radar keys on token_address (pair contract), not pair_address."""
        from app.domains.scanner.engines.new_token_radar import _fast_scan_token

        deployment = {
            "token_address": _TOKEN,  # pair/token contract — the radar's key
            "pair_address": "0x9999999999999999999999999999999999999999",
            "deployer": _DEPLOYER,
            "tx_hash": "0xabc",
            "block_timestamp": "2026-08-02 00:00:00",
        }
        with (
            _benign_fingerprint(),
            _benign_honeypot(),
            _good_liquidity(),
        ):
            out = await _fast_scan_token(deployment, "ethereum")

        assert out["token_address"] == _TOKEN
        assert out["token_address"] != "0x9999999999999999999999999999999999999999"
        assert out["deployer"] == _DEPLOYER

    @pytest.mark.asyncio
    async def test_f0_fallback_drops_deployments_without_token_address(self) -> None:
        """Deployments with only pair_address are skipped gracefully (no crash)."""
        with (
            patch(
                "app.domains.scanner.engines.new_token_radar._query_new_deployments",
                return_value=[
                    {
                        "deployer": _DEPLOYER,
                        "pair_address": "0x9999999999999999999999999999999999999999",
                        "block_timestamp": "2026-08-02 00:00:00",
                    }
                ],
            ),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await scan_new_tokens("ethereum", notify=False)
        assert out == []


class TestScoreNewToken:
    """Unit tests for the CRITICAL / HIGH / WARNING / INFO scoring heuristic."""

    @pytest.mark.parametrize(
        ("signals", "expected_level"),
        [
            # Honeypot flag -> CRITICAL regardless of other signals
            ({"is_honeypot": True}, "CRITICAL"),
            # REPEAT_RUG / FARM buckets -> CRITICAL
            ({"deployer_bucket": "REPEAT_RUG", "deployer_risk_score": 90}, "CRITICAL"),
            ({"deployer_bucket": "FARM", "deployer_risk_score": 85}, "CRITICAL"),
            # Deployer risk >= 80 -> CRITICAL
            ({"deployer_risk_score": 90}, "CRITICAL"),
            # Deployer risk >= 60 -> HIGH
            ({"deployer_risk_score": 70, "liquidity_usd": 5000}, "HIGH"),
            # Liquidity < $1K -> HIGH
            ({"deployer_risk_score": 50, "liquidity_usd": 500}, "HIGH"),
            # NEW / UNKNOWN buckets -> WARNING
            ({"deployer_bucket": "NEW", "deployer_risk_score": 30, "liquidity_usd": 5000}, "WARNING"),
            ({"deployer_bucket": "UNKNOWN", "deployer_risk_score": 50, "liquidity_usd": 5000}, "WARNING"),
            # Liquidity < $5K -> WARNING
            ({"deployer_risk_score": 50, "liquidity_usd": 2000}, "WARNING"),
            # Otherwise INFO
            (
                {"deployer_bucket": "REPEAT_CLEAN", "deployer_risk_score": 15, "liquidity_usd": 10000},
                "INFO",
            ),
        ],
    )
    def test_score_thresholds(self, signals: dict, expected_level: str) -> None:
        """Each signal combination maps to the documented risk level."""
        level, score = _score_new_token(signals)
        assert level == expected_level
        assert 0 <= score <= 100

    def test_score_values_for_key_thresholds(self) -> None:
        """Scores follow the documented numeric thresholds."""
        assert _score_new_token({"is_honeypot": True}) == ("CRITICAL", 100)
        assert _score_new_token({"deployer_bucket": "REPEAT_RUG", "deployer_risk_score": 90}) == (
            "CRITICAL",
            90,
        )
        assert _score_new_token(
            {"deployer_bucket": "REPEAT_CLEAN", "deployer_risk_score": 15, "liquidity_usd": 10000}
        ) == ("INFO", 7)


if __name__ == "__main__":
    pytest.main([__file__])
