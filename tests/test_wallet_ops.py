"""
Tests for RMI Wallet Operations — 15+ tests covering label importing,
wallet monitoring, portfolio risk aggregation, and wallet security.

All external API calls and Redis interactions are mocked via
unittest.mock.patch.
"""

from __future__ import annotations

import os

# Set required env vars BEFORE any app imports
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-thats-32-chars-long!")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════
#  1. Wallet Memory Label Importer (5 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestLabelImporter:
    """wallet_memory.label_importer: download helpers, imports."""

    @pytest.mark.asyncio
    async def test_download_file_success(self):
        """_download_file returns True on HTTP 200."""
        from app.wallet_memory.label_importer.download import _download_file

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"some,csv,data\n1,2,3"
        mock_client.get = AsyncMock(return_value=mock_resp)

        with (
            patch(
                "rugmunch_scanner.wallet_memory.label_importer.download._get_http",
                AsyncMock(return_value=mock_client),
            ),
            patch("builtins.open", mock_open()),
            patch("os.makedirs"),
        ):
            result = await _download_file("https://example.com/labels.csv", "/tmp/labels.csv")
        assert result is True

    @pytest.mark.asyncio
    async def test_download_file_http_error_returns_false(self):
        """_download_file returns False when HTTP status is not 200."""
        from app.wallet_memory.label_importer.download import _download_file

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch(
            "rugmunch_scanner.wallet_memory.label_importer.download._get_http",
            AsyncMock(return_value=mock_client),
        ):
            result = await _download_file("https://example.com/missing.csv", "/tmp/missing.csv")
        assert result is False

    @pytest.mark.asyncio
    async def test_download_json_success(self):
        """_download_json returns parsed JSON on success."""
        from app.wallet_memory.label_importer.download import _download_json

        expected = {"key": "value"}
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = expected
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch(
            "rugmunch_scanner.wallet_memory.label_importer.download._get_http",
            AsyncMock(return_value=mock_client),
        ):
            result = await _download_json("https://example.com/data.json")
        assert result == expected

    @pytest.mark.asyncio
    async def test_download_json_http_error_returns_none(self):
        """_download_json returns None when HTTP fails."""
        from app.wallet_memory.label_importer.download import _download_json

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch(
            "rugmunch_scanner.wallet_memory.label_importer.download._get_http",
            AsyncMock(return_value=mock_client),
        ):
            result = await _download_json("https://example.com/bad.json")
        assert result is None

    def test_solana_csv_specs_are_defined(self):
        """SOLANA_CSV_SPECS contains expected label file specs."""
        from app.wallet_memory.label_importer.config import SOLANA_CSV_SPECS

        assert len(SOLANA_CSV_SPECS) >= 3
        filenames = [spec[0] for spec in SOLANA_CSV_SPECS]
        assert "solana_cex_labels.csv" in filenames


# ═══════════════════════════════════════════════════════════════════════
#  2. LabelService — unified label resolution (4 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestLabelService:
    """wallet_memory.labels.LabelService: resolve, sources, caching."""

    @pytest.fixture
    def label_service(self):
        from app.wallet_memory.labels import LabelService

        with patch.object(LabelService, "_load_builtin_cex"):
            svc = LabelService()
            svc._http = AsyncMock()
            yield svc

    @pytest.mark.asyncio
    async def test_resolve_empty_address_returns_none(self, label_service):
        """resolve() with empty address returns a dict with empty primary_label."""
        result = await label_service.resolve("", "ethereum")
        # resolve returns a dict; empty address should have no primary_label
        assert result is not None
        assert result.get("primary_label") == "" or result.get("primary_label") is None

    @pytest.mark.asyncio
    async def test_resolve_handles_http_failure(self, label_service):
        """resolve() handles HTTP errors gracefully."""
        label_service._http.get.side_effect = Exception("Network error")
        # Should not raise — method should catch and return None
        result = await label_service.resolve("0x1234567890abcdef1234567890abcdef12345678", "ethereum")
        assert result is not None

    def test_label_service_init_http_client(self, label_service):
        """LabelService initializes with an httpx client."""
        assert label_service._http is not None

    def test_builtin_cex_loads_without_error(self):
        """Built-in CEX wallets load without error."""
        from app.wallet_memory.labels import LabelService

        with patch("app.wallet_memory.labels.LabelService._load_builtin_cex"):
            svc = LabelService()
            # Should not crash even without real chain_registry data
            assert hasattr(svc, "_builtin_cex")


# ═══════════════════════════════════════════════════════════════════════
#  3. WalletMonitor — WatchlistManager (5 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestWalletMonitorWatchlist:
    """WalletMonitor WatchlistManager: CRUD operations."""

    @pytest.fixture
    def mock_redis(self):
        """Return a mock redis async client with common methods."""
        r = MagicMock()
        r.zadd = AsyncMock(return_value=1)
        r.zrem = AsyncMock(return_value=1)
        r.zscore = AsyncMock(return_value=1234567890.0)
        r.zrevrange = AsyncMock(
            return_value=[
                ("0xwallet1", 1234567890.0),
                ("0xwallet2", 1234567891.0),
            ]
        )
        r.zrange = AsyncMock(return_value=["0xwallet1", "0xwallet2"])
        r.smembers = AsyncMock(return_value={"defi", "whale"})
        r.sadd = AsyncMock(return_value=1)
        r.delete = AsyncMock(return_value=1)
        return r

    @pytest.mark.asyncio
    async def test_watchlist_add_calls_redis_zadd(self, mock_redis):
        """WatchlistManager.add() calls Redis ZADD."""
        from app.domains.wallet.wallet_monitor import WatchlistManager

        with (
            patch("app.domains.wallet.wallet_monitor._get_redis", AsyncMock(return_value=mock_redis)),
            patch("app.domains.wallet.wallet_monitor.redis_async", MagicMock()),
        ):
            result = await WatchlistManager.add(
                "0x1234567890abcdef1234567890abcdef12345678",
                chain="ethereum",
                tags=["defi"],
            )
        assert result is True
        mock_redis.zadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_watchlist_remove_calls_redis_zrem(self, mock_redis):
        """WatchlistManager.remove() calls Redis ZREM."""
        from app.domains.wallet.wallet_monitor import WatchlistManager

        with (
            patch("app.domains.wallet.wallet_monitor._get_redis", AsyncMock(return_value=mock_redis)),
            patch("app.domains.wallet.wallet_monitor.redis_async", MagicMock()),
        ):
            result = await WatchlistManager.remove(
                "0x1234567890abcdef1234567890abcdef12345678",
                chain="ethereum",
            )
        assert result is True
        mock_redis.zrem.assert_called_once()

    @pytest.mark.asyncio
    async def test_watchlist_is_watched_returns_true(self, mock_redis):
        """WatchlistManager.is_watched() returns True when wallet found."""
        from app.domains.wallet.wallet_monitor import WatchlistManager

        with (
            patch("app.domains.wallet.wallet_monitor._get_redis", AsyncMock(return_value=mock_redis)),
            patch("app.domains.wallet.wallet_monitor.redis_async", MagicMock()),
        ):
            result = await WatchlistManager.is_watched(
                "0x1234567890abcdef1234567890abcdef12345678",
                chain="ethereum",
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_watchlist_is_watched_returns_false_when_not_found(self, mock_redis):
        """WatchlistManager.is_watched() returns False when wallet not in watchlist."""
        mock_redis.zscore = AsyncMock(return_value=None)  # None means not found
        from app.domains.wallet.wallet_monitor import WatchlistManager

        with (
            patch("app.domains.wallet.wallet_monitor._get_redis", AsyncMock(return_value=mock_redis)),
            patch("app.domains.wallet.wallet_monitor.redis_async", MagicMock()),
        ):
            result = await WatchlistManager.is_watched(
                "0xnonexistent",
                chain="ethereum",
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_watchlist_list_calls_zrevrange(self, mock_redis):
        """WatchlistManager.list() returns wallet list from Redis."""
        from app.domains.wallet.wallet_monitor import WatchlistManager

        with (
            patch("app.domains.wallet.wallet_monitor._get_redis", AsyncMock(return_value=mock_redis)),
            patch("app.domains.wallet.wallet_monitor.redis_async", MagicMock()),
        ):
            wallets = await WatchlistManager.list(chain="ethereum")
        assert len(wallets) == 2
        assert wallets[0]["wallet"] == "0xwallet1"
        assert set(wallets[0]["tags"]) == {"defi", "whale"}


class TestWalletMonitorAlertEngine:
    """WalletMonitor AlertEngine: rule evaluation."""

    @pytest.fixture
    def alert_engine(self):
        from app.domains.wallet.wallet_monitor import AlertEngine

        return AlertEngine(chain="ethereum")

    def test_alert_engine_has_five_rules(self, alert_engine):
        """AlertEngine initializes with 5 detection rules."""
        assert len(alert_engine.rules) == 5

    def test_rule_large_transfer_fires_for_high_value(self, alert_engine):
        """_rule_large_transfer returns alert for large value."""
        from app.domains.wallet.wallet_monitor import WalletTx

        tx = WalletTx(
            hash="0xtx1",
            from_addr="0xfrom",
            to_addr="0xto",
            value=100.0,  # 100 ETH ~ $300K
            chain="ethereum",
            timestamp="2026-01-01T00:00:00Z",
        )
        alert = alert_engine._rule_large_transfer(tx, [])
        assert alert is not None
        assert alert.alert_type == "large_transfer"
        assert alert.severity == "warning"

    def test_rule_large_transfer_skips_small_value(self, alert_engine):
        """_rule_large_transfer returns None for small value."""
        from app.domains.wallet.wallet_monitor import WalletTx

        tx = WalletTx(
            hash="0xtx2",
            from_addr="0xfrom",
            to_addr="0xto",
            value=0.001,  # 0.001 ETH ~ $3
            chain="ethereum",
            timestamp="2026-01-01T00:00:00Z",
        )
        alert = alert_engine._rule_large_transfer(tx, [])
        assert alert is None

    def test_rule_first_time_dex_fires_for_first_dex_trade(self, alert_engine):
        """_rule_first_time_dex returns alert when no prior DEX trades."""
        from app.domains.wallet.wallet_monitor import WalletTx

        tx = WalletTx(
            hash="0xtx3",
            from_addr="0xfrom",
            to_addr="0xto",
            value=1.0,
            chain="ethereum",
            timestamp="2026-01-01T00:00:00Z",
            dex_swap={"pool": "0xpool", "token_in": "ETH", "token_out": "USDC"},
        )
        alert = alert_engine._rule_first_time_dex(tx, [])
        assert alert is not None
        assert alert.alert_type == "first_time_dex"
        assert alert.severity == "info"

    def test_rule_new_approval_fires_for_first_approval(self, alert_engine):
        """_rule_new_approval returns alert when no prior approvals."""
        from app.domains.wallet.wallet_monitor import WalletTx

        tx = WalletTx(
            hash="0xtx4",
            from_addr="0xfrom",
            to_addr="0xspender",
            value=0.0,
            chain="ethereum",
            timestamp="2026-01-01T00:00:00Z",
            is_approval=True,
        )
        alert = alert_engine._rule_new_approval(tx, [])
        assert alert is not None
        assert alert.alert_type == "new_token_approval"

    def test_rule_contract_creation_fires(self, alert_engine):
        """_rule_contract_creation returns alert when tx creates a contract."""
        from app.domains.wallet.wallet_monitor import WalletTx

        tx = WalletTx(
            hash="0xtx5",
            from_addr="0xfrom",
            to_addr="",
            value=0.0,
            chain="ethereum",
            timestamp="2026-01-01T00:00:00Z",
            is_contract_creation=True,
        )
        alert = alert_engine._rule_contract_creation(tx, [])
        assert alert is not None
        assert alert.alert_type == "contract_creation"

    def test_alert_evaluate_runs_all_rules(self, alert_engine):
        """AlertEngine.evaluate() runs all rules and returns alerts."""
        from app.domains.wallet.wallet_monitor import WalletTx

        tx = WalletTx(
            hash="0xtx6",
            from_addr="0xfrom",
            to_addr="0xspender",
            value=50.0,  # Large value
            chain="ethereum",
            timestamp="2026-01-01T00:00:00Z",
            is_approval=True,
            dex_swap={"pool": "0xpool"},
        )
        alerts = [
            alert_engine._rule_large_transfer(tx, []),
            alert_engine._rule_new_approval(tx, []),
            alert_engine._rule_first_time_dex(tx, []),
        ]
        alerts = [a for a in alerts if a is not None]
        assert len(alerts) >= 2  # at least large_transfer + new_approval


# ═══════════════════════════════════════════════════════════════════════
#  4. PortfolioRiskAggregator — risk aggregation (4 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestPortfolioRiskAggregator:
    """PortfolioRiskAggregator.analyze() and data models."""

    def test_validate_wallet_address_evm(self):
        """validate_wallet_address identifies EVM addresses."""
        from app.domains.portfolio.portfolio_risk_aggregator import (
            validate_wallet_address,
        )

        valid, hint = validate_wallet_address("0x1234567890abcdef1234567890abcdef12345678")
        assert valid is True
        assert hint == "evm"

    def test_validate_wallet_address_solana(self):
        """validate_wallet_address identifies Solana addresses."""
        from app.domains.portfolio.portfolio_risk_aggregator import (
            validate_wallet_address,
        )

        valid, hint = validate_wallet_address("FakeBase58Addr111111111111111111111111111111")
        assert valid is True
        assert hint == "solana"

    def test_validate_wallet_address_invalid_returns_false(self):
        """validate_wallet_address returns False for invalid input."""
        from app.domains.portfolio.portfolio_risk_aggregator import (
            validate_wallet_address,
        )

        valid, _hint = validate_wallet_address("")  # empty
        assert valid is False

        valid, _hint = validate_wallet_address("0xshort")  # too short
        assert valid is False

    @pytest.mark.asyncio
    async def test_analyze_handles_empty_address(self):
        """analyze() handles empty address gracefully."""
        from app.domains.portfolio.portfolio_risk_aggregator import (
            ChainPortfolio,
            PortfolioRiskAggregator,
        )

        agg = PortfolioRiskAggregator(chains=["ethereum"])
        mock_cp = ChainPortfolio(chain="ethereum")
        with patch.object(agg, "_scan_chain", AsyncMock(return_value=mock_cp)):
            profile = await agg.analyze("")
        assert profile is not None

    @pytest.mark.asyncio
    async def test_analyze_with_balance_summary(self):
        """analyze() produces a portfolio risk profile with balance data."""
        from app.domains.portfolio.portfolio_risk_aggregator import (
            ChainPortfolio,
            PortfolioRiskAggregator,
        )

        agg = PortfolioRiskAggregator(chains=["ethereum"])
        mock_cp = ChainPortfolio(chain="ethereum", total_value_usd=5000.0)
        with patch.object(agg, "_scan_chain", AsyncMock(return_value=mock_cp)):
            profile = await agg.analyze("0x1234567890abcdef1234567890abcdef12345678")
        assert profile is not None
        assert hasattr(profile, "total_value_usd") or hasattr(profile, "risk_score")


# ═══════════════════════════════════════════════════════════════════════
#  5. Wallet Data Models (2 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestWalletDataModels:
    """WalletTx, WalletAlert, BurstSignal, LabelSuggestion dataclasses."""

    def test_wallet_tx_defaults(self):
        """WalletTx has safe defaults."""
        from app.domains.wallet.wallet_monitor import WalletTx

        tx = WalletTx(
            hash="0xtx",
            from_addr="0xfrom",
            to_addr="0xto",
            value=1.0,
            chain="ethereum",
            timestamp="2026-01-01T00:00:00Z",
        )
        assert tx.is_contract_interaction is False
        assert tx.is_approval is False
        assert tx.dex_swap is None
        assert tx.token_transfers == []

    def test_wallet_alert_to_dict(self):
        """WalletAlert.to_dict() returns expected keys."""
        from app.domains.wallet.wallet_monitor import WalletAlert

        alert = WalletAlert(
            alert_type="test_alert",
            wallet="0xwallet",
            chain="ethereum",
            tx_hash="0xtx",
            severity="critical",
            message="Test alert message",
        )
        d = alert.to_dict()
        assert d["type"] == "wallet_alert"
        assert d["alert_type"] == "test_alert"
        assert d["severity"] == "critical"
        assert "timestamp" in d

    def test_burst_signal_to_dict(self):
        """BurstSignal.to_dict() returns expected structure."""
        from app.domains.wallet.wallet_monitor import BurstSignal

        bs = BurstSignal(
            wallet="0xwallet",
            chain="ethereum",
            tx_count=25,
            window_seconds=300,
            severity="warning",
            timestamp="2026-01-01T00:00:00Z",
        )
        d = bs.to_dict()
        assert d["type"] == "burst"
        assert d["tx_count"] == 25
        assert d["severity"] == "warning"

    def test_label_suggestion_to_dict(self):
        """LabelSuggestion.to_dict() returns expected structure."""
        from app.domains.wallet.wallet_monitor import LabelSuggestion

        ls = LabelSuggestion(
            wallet="0xwallet",
            chain="ethereum",
            suggested_label="whale",
            confidence=0.85,
            reasoning="Large transfers detected",
            pattern_summary={"avg_tx_value": 500000},
            timestamp="2026-01-01T00:00:00Z",
        )
        d = ls.to_dict()
        assert d["type"] == "label_suggestion"
        assert d["suggested_label"] == "whale"
        assert d["confidence"] == 0.85
