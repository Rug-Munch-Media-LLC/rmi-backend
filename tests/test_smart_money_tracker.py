"""
Tests for Smart Money Flow Tracker.
"""

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

# Ensure app is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.smart_money_tracker import (
    DetectedMove,
    FlowReport,
    ImpactLevel,
    MoveDirection,
    SmartMoneyTracker,
    SmartWallet,
    WalletCategory,
    run_smart_money_scan,
)

# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_wallets():
    """Create sample smart wallets for testing."""
    return [
        SmartWallet(
            address="0x1234567890abcdef1234567890abcdef12345678",
            chain="ethereum",
            label="Test Whale",
            category=WalletCategory.WHALE,
            reputation_score=85.0,
            confidence=0.8,
            tags=["whale", "test"],
            total_value_usd=5_000_000,
        ),
        SmartWallet(
            address="A3eME5C1Z7YpEkna5K5WJs8xJ9KXGxFwVvxn3BbmxM7G",
            chain="solana",
            label="Solana Whale",
            category=WalletCategory.WHALE,
            reputation_score=70.0,
            confidence=0.6,
            tags=["solana"],
            total_value_usd=2_500_000,
        ),
        SmartWallet(
            address="0xabcdef1234567890abcdef1234567890abcdef12",
            chain="ethereum",
            label="Market Maker",
            category=WalletCategory.MARKET_MAKER,
            reputation_score=90.0,
            confidence=0.9,
            tags=["mm", "high-freq"],
            total_value_usd=50_000_000,
        ),
    ]


@pytest.fixture
def tracker(temp_data_dir, sample_wallets):
    """Create tracker with sample wallets."""
    t = SmartMoneyTracker(data_dir=temp_data_dir)
    t._wallets = sample_wallets
    t._save_wallets()
    return t


# ═══════════════════════════════════════════════════════════════
# SmartWallet Tests
# ═══════════════════════════════════════════════════════════════


class TestSmartWallet:
    def test_summary_formats_correctly(self):
        """Test that summary() produces expected output format."""
        wallet = SmartWallet(
            address="0x1234567890abcdef1234567890abcdef12345678",
            chain="ethereum",
            label="Test Wallet",
            category=WalletCategory.WHALE,
            total_value_usd=1_000_000,
            reputation_score=75.0,
        )
        summary = wallet.summary()
        assert "Test Wallet" in summary
        assert "ethereum" in summary
        assert "1,000,000" in summary
        assert "75/100" in summary
        assert "🐋" in summary  # Whale icon

    def test_vc_fund_category_icon(self):
        """Test that VC fund has correct icon."""
        wallet = SmartWallet(
            address="0xabc",
            chain="ethereum",
            label="Paradigm",
            category=WalletCategory.VC_FUND,
        )
        assert "🏦" in wallet.summary()

    def test_category_scores(self):
        """Test that reputation scores transfer correctly."""
        wallet = SmartWallet(
            address="0xabc",
            chain="ethereum",
            label="Test",
            category=WalletCategory.TOP_TRADER,
            reputation_score=88.0,
        )
        assert wallet.reputation_score == 88.0

    def test_default_values(self):
        """Test default values for optional fields."""
        wallet = SmartWallet(
            address="0xabc",
            chain="ethereum",
            label="Test",
            category=WalletCategory.UNKNOWN,
        )
        assert wallet.tags == []
        assert wallet.total_value_usd == 0.0
        assert wallet.reputation_score == 50.0
        assert wallet.confidence == 0.5
        assert wallet.tx_count == 0


# ═══════════════════════════════════════════════════════════════
# DetectedMove Tests
# ═══════════════════════════════════════════════════════════════


class TestDetectedMove:
    @pytest.fixture
    def sample_wallet(self):
        return SmartWallet(
            address="0x1234",
            chain="ethereum",
            label="Test Whale",
            category=WalletCategory.WHALE,
        )

    def test_enter_move_summary(self, sample_wallet):
        """Test that enter moves show green icon."""
        move = DetectedMove(
            wallet=sample_wallet,
            token_address="0xtoken",
            token_symbol="RUG",
            chain="ethereum",
            direction=MoveDirection.ENTER,
            tx_hash="0xabc123",
            timestamp=datetime.now(UTC),
            amount=1000,
            value_usd=50_000,
            impact=ImpactLevel.HIGH,
            context="Opened new position",
        )
        summary = move.summary()
        assert "🟢" in summary  # Enter icon
        assert "ENTER" in summary
        assert "RUG" in summary
        # Summary shows $SYMBOL (amount) format, not $SYMBOL ($amount)
        assert "50,000" in summary
        assert "⚠️" in summary  # High impact icon

    def test_exit_move_summary(self, sample_wallet):
        """Test that exit moves show red icon."""
        move = DetectedMove(
            wallet=sample_wallet,
            token_address="0xtoken",
            token_symbol="BTC",
            chain="ethereum",
            direction=MoveDirection.EXIT,
            tx_hash="0xdef456",
            timestamp=datetime.now(UTC),
            amount=5,
            value_usd=500_000,
            impact=ImpactLevel.CRITICAL,
            context="Full exit from BTC position",
        )
        summary = move.summary()
        assert "🔴" in summary
        assert "EXIT" in summary
        assert "🚨" in summary  # Critical icon

    def test_medium_impact_shows_correctly(self, sample_wallet):
        """Test medium impact formatting."""
        move = DetectedMove(
            wallet=sample_wallet,
            token_address="",
            token_symbol="ETH",
            chain="ethereum",
            direction=MoveDirection.ADD,
            tx_hash="0xghi789",
            timestamp=datetime.now(UTC),
            amount=10,
            value_usd=30_000,
            impact=ImpactLevel.MEDIUM,
            context="Added to position",
        )
        summary = move.summary()
        assert "📊" in summary
        assert "ADD" in summary in summary


# ═══════════════════════════════════════════════════════════════
# FlowReport Tests
# ═══════════════════════════════════════════════════════════════


class TestFlowReport:
    @pytest.fixture
    def sample_wallet(self):
        return SmartWallet(
            address="0x1234",
            chain="ethereum",
            label="Whale",
            category=WalletCategory.WHALE,
        )

    def make_move(self, wallet, value, impact, direction=MoveDirection.ENTER):
        return DetectedMove(
            wallet=wallet,
            token_address="0xtoken",
            token_symbol="TEST",
            chain="ethereum",
            direction=direction,
            tx_hash=f"0x{hash(str(value))}",
            timestamp=datetime.now(UTC),
            amount=100,
            value_usd=value,
            impact=impact,
            context=f"Test move ${value}",
        )

    def test_top_moves_sorts_by_impact_then_value(self, sample_wallet):
        """Test that top_moves returns highest impact first, sorted by value."""
        report = FlowReport()
        report.moves = [
            self.make_move(sample_wallet, 200_000, ImpactLevel.MEDIUM),
            self.make_move(sample_wallet, 1_000_000, ImpactLevel.CRITICAL),
            self.make_move(sample_wallet, 50_000, ImpactLevel.LOW),
            self.make_move(sample_wallet, 750_000, ImpactLevel.HIGH),
        ]

        top = report.top_moves(limit=3)
        assert len(top) == 3
        assert top[0].impact == ImpactLevel.CRITICAL
        assert top[1].impact == ImpactLevel.HIGH
        assert top[2].impact == ImpactLevel.MEDIUM

    def test_by_impact_filters_correctly(self, sample_wallet):
        """Test filtering by impact level."""
        report = FlowReport()
        report.moves = [
            self.make_move(sample_wallet, 1_000_000, ImpactLevel.CRITICAL),
            self.make_move(sample_wallet, 500_000, ImpactLevel.HIGH),
            self.make_move(sample_wallet, 100_000, ImpactLevel.MEDIUM),
        ]

        critical_moves = report.by_impact(ImpactLevel.CRITICAL)
        assert len(critical_moves) == 1
        assert critical_moves[0].value_usd == 1_000_000

    def test_summary_includes_stats(self, sample_wallet):
        """Test that summary contains key statistics."""
        report = FlowReport()
        report.moves = [
            self.make_move(sample_wallet, 1_000_000, ImpactLevel.CRITICAL),
            self.make_move(sample_wallet, 500_000, ImpactLevel.HIGH),
            self.make_move(sample_wallet, 100_000, ImpactLevel.MEDIUM),
        ]
        report.wallets_scanned = 10
        report.chains_covered = ["ethereum", "solana"]
        report.scan_duration_ms = 1500

        summary = report.summary()
        assert "Smart Money Flow Report" in summary
        assert "10 wallets" in summary
        assert "ethereum" in summary
        assert "solana" in summary
        assert "$1,600,000" in summary or "$1,600,000" in summary


# ═══════════════════════════════════════════════════════════════
# SmartMoneyTracker Tests
# ═══════════════════════════════════════════════════════════════


class TestSmartMoneyTracker:
    def test_init_creates_data_dir(self, temp_data_dir):
        """Test that tracker creates its data directory."""
        SmartMoneyTracker(data_dir=temp_data_dir)
        assert os.path.exists(temp_data_dir)

    def test_add_wallet(self, tracker, temp_data_dir):
        """Test adding a new wallet to the registry."""
        new_wallet = SmartWallet(
            address="0xnew",
            chain="ethereum",
            label="New Whale",
            category=WalletCategory.WHALE,
        )
        count_before = tracker.get_wallet_count()
        tracker.add_wallet(new_wallet)
        assert tracker.get_wallet_count() == count_before + 1

        # Verify persistence
        tracker2 = SmartMoneyTracker(data_dir=temp_data_dir)
        assert tracker2.get_wallet_count() == count_before + 1

    def test_add_wallet_updates_existing(self, tracker):
        """Test that adding an existing wallet updates it."""
        updated = SmartWallet(
            address="0x1234567890abcdef1234567890abcdef12345678",
            chain="ethereum",
            label="Test Whale UPDATED",
            category=WalletCategory.WHALE,
            reputation_score=95.0,
        )
        tracker.add_wallet(updated)
        wallet = tracker.get_wallets(chain="ethereum")[0]
        # First wallet should now be the updated one
        assert wallet.label == "Test Whale UPDATED"
        assert wallet.reputation_score == 95.0

    def test_remove_wallet(self, tracker):
        """Test wallet removal."""
        count_before = tracker.get_wallet_count()
        result = tracker.remove_wallet("0x1234567890abcdef1234567890abcdef12345678", "ethereum")
        assert result is True
        assert tracker.get_wallet_count() == count_before - 1

    def test_remove_nonexistent_wallet(self, tracker):
        """Test removing a wallet that doesn't exist."""
        result = tracker.remove_wallet("0xnonexistent", "ethereum")
        assert result is False

    def test_get_wallets_filter_by_category(self, tracker):
        """Test filtering wallets by category."""
        whales = tracker.get_wallets(category=WalletCategory.WHALE)
        assert len(whales) == 2  # Our fixture has 2 whales

        mm = tracker.get_wallets(category=WalletCategory.MARKET_MAKER)
        assert len(mm) == 1

    def test_get_wallets_filter_by_chain(self, tracker):
        """Test filtering wallets by chain."""
        eth_wallets = tracker.get_wallets(chain="ethereum")
        assert len(eth_wallets) == 2  # 2 eth wallets in fixture

        sol_wallets = tracker.get_wallets(chain="solana")
        assert len(sol_wallets) == 1

    def test_get_wallets_filter_none(self, tracker):
        """Test getting all wallets with no filters."""
        wallets = tracker.get_wallets()
        assert len(wallets) == 3

    def test_default_wallets_loaded(self, temp_data_dir):
        """Test that default wallets are loaded when no saved data."""
        tracker = SmartMoneyTracker(data_dir=temp_data_dir)
        # Should have the default wallet set
        assert tracker.get_wallet_count() == 10  # 10 default wallets

    def test_score_impact_critical(self, tracker):
        """Test critical impact scoring."""
        wallet = SmartWallet(
            address="0xcrit",
            chain="ethereum",
            label="Critical",
            category=WalletCategory.WHALE,
            reputation_score=85.0,
        )
        impact = tracker._score_impact(wallet, 2_000_000, MoveDirection.ENTER)
        assert impact == ImpactLevel.CRITICAL

    def test_score_impact_high(self, tracker):
        """Test high impact scoring."""
        wallet = SmartWallet(
            address="0xhigh",
            chain="ethereum",
            label="High",
            category=WalletCategory.WHALE,
            reputation_score=70.0,
        )
        impact = tracker._score_impact(wallet, 600_000, MoveDirection.REDUCE)
        assert impact == ImpactLevel.HIGH

    def test_score_impact_medium(self, tracker):
        """Test medium impact scoring."""
        wallet = SmartWallet(
            address="0xmed",
            chain="ethereum",
            label="Medium",
            category=WalletCategory.WHALE,
            reputation_score=60.0,
        )
        impact = tracker._score_impact(wallet, 150_000, MoveDirection.ADD)
        assert impact == ImpactLevel.MEDIUM

    def test_score_impact_low(self, tracker):
        """Test low impact scoring."""
        wallet = SmartWallet(
            address="0xlow",
            chain="ethereum",
            label="Low",
            category=WalletCategory.TOP_TRADER,
            reputation_score=55.0,
        )
        impact = tracker._score_impact(wallet, 20_000, MoveDirection.TRANSFER)
        assert impact == ImpactLevel.LOW

    def test_generate_context_enter(self, tracker):
        """Test context generation for enter moves."""
        wallet = SmartWallet(
            address="0xctx",
            chain="ethereum",
            label="Alpha Whale",
            category=WalletCategory.WHALE,
        )
        ctx = tracker._generate_context(MoveDirection.ENTER, wallet, 100_000)
        assert "Alpha Whale" in ctx
        assert "opened a new position" in ctx
        assert "$100,000" in ctx

    def test_generate_context_exit(self, tracker):
        """Test context generation for exit moves."""
        wallet = SmartWallet(
            address="0xctx",
            chain="ethereum",
            label="Trader",
            category=WalletCategory.TOP_TRADER,
        )
        ctx = tracker._generate_context(MoveDirection.EXIT, wallet, 500_000)
        assert "fully exited" in ctx

    def test_generate_context_add(self, tracker):
        """Test context generation for add moves."""
        wallet = SmartWallet(
            address="0xctx", chain="ethereum", label="Whale", category=WalletCategory.WHALE
        )
        ctx = tracker._generate_context(MoveDirection.ADD, wallet, 50_000)
        assert "added" in ctx

    def test_generate_context_reduce(self, tracker):
        """Test context generation for reduce moves."""
        wallet = SmartWallet(
            address="0xctx", chain="ethereum", label="Whale", category=WalletCategory.WHALE
        )
        ctx = tracker._generate_context(MoveDirection.REDUCE, wallet, 50_000)
        assert "reduced" in ctx

    @pytest.mark.asyncio
    async def test_scan_empty_tracker(self, temp_data_dir):
        """Test scanning with no wallets."""
        tracker = SmartMoneyTracker(data_dir=temp_data_dir)
        # Override wallets to empty
        tracker._wallets = []
        report = await tracker.scan(lookback_minutes=60, max_wallets=10)
        assert len(report.moves) == 0
        assert report.wallets_scanned == 0
        assert report.scan_duration_ms > 0

    @pytest.mark.asyncio
    async def test_scan_with_mock_txs(self, tracker):
        """Test scan with mocked transaction data."""
        # Mock the fetch method to return sample data
        sample_tx = {
            "tx_hash": "0xabc123def456",
            "timestamp": datetime.now(UTC) - timedelta(minutes=30),
            "chain": "ethereum",
            "type": "transfer",
            "from": "0x1234567890abcdef1234567890abcdef12345678",
            "to": "0x9876543210fedcba9876543210fedcba98765432",
            "value_usd": 100_000,
            "token_address": "0xtoken123",
            "token_symbol": "RUG",
            "raw": {},
        }

        with (
            patch.object(tracker, "_fetch_recent_tx", new=AsyncMock(return_value=[sample_tx])),
            patch.object(tracker, "_get_token_safety", new=AsyncMock(return_value=45.0)),
        ):
            report = await tracker.scan(lookback_minutes=120, max_wallets=3)

            # Should have detected moves
            assert len(report.moves) > 0
            assert report.wallets_scanned == 3
            assert "ethereum" in report.chains_covered

    def test_get_report_path(self, tracker, temp_data_dir):
        """Test report path returns correct directory."""
        assert tracker.get_report_path() == temp_data_dir

    def test_wallet_count(self, tracker):
        """Test get_wallet_count returns correct count."""
        assert tracker.get_wallet_count() == 3


# ═══════════════════════════════════════════════════════════════
# Entry Point Test
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_run_smart_money_scan_callable():
    """Test that the module-level convenience function is callable."""
    # This just tests the function signature exists
    assert callable(run_smart_money_scan)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
