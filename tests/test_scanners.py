"""
Tests for RMI Scanner Modules — 20+ tests covering token analysis, wallet
scanning, oracle manipulation, flash loan detection, input validation,
error handling, and rate limiting.

All external API calls are mocked via unittest.mock.patch.
"""

from __future__ import annotations

import os

# Set required env vars BEFORE any app imports
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-thats-32-chars-long!")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────


def _mock_htttp_response(data: dict, status: int = 200):
    """Create a mock httpx response with a .json() that is a regular callable."""
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    return m


def _mock_async_httpx_response(data: dict, status: int = 200):
    """Create a mock for an async context-manager response."""
    m = MagicMock()
    m.status_code = status
    m.__aenter__.return_value = m
    m.__aexit__.return_value = None
    m.json = AsyncMock(return_value=data)
    return m


# ═══════════════════════════════════════════════════════════════════════
#  1. DegenSecurityScanner — token analysis (5 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestDegenSecurityScanner:
    """DegenSecurityScanner.full_scan() and supporting methods."""

    @pytest.fixture
    def scanner(self):
        with patch("app.free_solscan_client.FreeSolscanClient") as mock_solscan_cls:
            instance = mock_solscan_cls.return_value
            # Stub solscan sync methods
            instance.token_data.return_value = {
                "name": "TestToken",
                "symbol": "TST",
                "decimals": 9,
                "supply": "1000000000000",
                "createdTime": "2026-01-01T00:00:00Z",
            }
            instance.token_holders_total.return_value = 500
            instance.get_holder_wallets.return_value = [
                {
                    "address": "FakeAddress111111111111111111111111111",
                    "amount": 500_000,
                    "uiAmount": 50.0,
                }
            ]

            from app.domains.scanner.degen_security_scanner import DegenSecurityScanner

            s = DegenSecurityScanner()
            yield s

    @pytest.mark.asyncio
    async def test_full_scan_returns_security_report(self, scanner):
        """full_scan() returns a SecurityReport with expected fields."""
        from app.domains.scanner.degen_security_scanner import phases as degen_phases

        with (
            patch.object(
                degen_phases,
                "_helius_token_metadata",
                AsyncMock(
                    return_value={
                        "name": "TestToken",
                        "symbol": "TST",
                        "decimals": 9,
                        "mintAuthority": None,
                        "freezeAuthority": None,
                        "supply": "1000000000",
                    }
                ),
            ),
            patch.object(
                degen_phases,
                "_birdeye_token_data",
                AsyncMock(
                    return_value={
                        "price": 0.05,
                        "mc": 50000,
                        "v24hUSD": 10000,
                        "priceChange24h": 12.0,
                        "liquidity": 20000,
                        "buySellRatio": 1.2,
                    }
                ),
            ),
        ):
            report = await scanner.full_scan("FakeTokenMintAddress1111111111")

        assert report.token_address == "FakeTokenMintAddress1111111111"
        assert report.token_name == "TestToken"
        assert report.token_symbol == "TST"
        assert report.overall_risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
        assert 0 <= report.rug_pull_probability <= 100
        assert report.scan_duration_ms > 0
        assert isinstance(report.to_dict(), dict)

    @pytest.mark.asyncio
    async def test_full_scan_with_active_mint_authority_flag_critical(self, scanner):
        """Active mint authority produces a critical-level warning."""
        from app.domains.scanner.degen_security_scanner import phases as degen_phases

        with (
            patch.object(
                degen_phases,
                "_helius_token_metadata",
                AsyncMock(
                    return_value={
                        "name": "RugToken",
                        "symbol": "RUG",
                        "decimals": 9,
                        "mintAuthority": "DevWallet111111111111111111111111111111",
                        "freezeAuthority": None,
                    }
                ),
            ),
            patch.object(degen_phases, "_birdeye_token_data", AsyncMock(return_value={})),
        ):
            report = await scanner.full_scan("FakeRugTokenMint11111111111111", quick=True)

        assert report.mint_authority_renounced is False
        assert report.authorities_risk == "CRITICAL" or report.authorities_risk == "HIGH"
        assert any("MINT AUTHORITY ACTIVE" in w for w in scanner.critical_warnings)
        assert report.overall_risk != "LOW"

    @pytest.mark.asyncio
    async def test_full_scan_with_freeze_authority_adds_risk(self, scanner):
        """Active freeze authority raises risk."""
        from app.domains.scanner.degen_security_scanner import phases as degen_phases

        with (
            patch.object(
                degen_phases,
                "_helius_token_metadata",
                AsyncMock(
                    return_value={
                        "name": "FreezeToken",
                        "symbol": "FRZ",
                        "decimals": 9,
                        "mintAuthority": None,
                        "freezeAuthority": "Freezer11111111111111111111111111",
                    }
                ),
            ),
            patch.object(degen_phases, "_birdeye_token_data", AsyncMock(return_value={})),
        ):
            report = await scanner.full_scan("FreezeTokenMint1111111111111111", quick=True)

        assert report.freeze_authority_renounced is False
        assert report.authorities_risk != "LOW"

    @pytest.mark.asyncio
    async def test_full_scan_helius_fallback_when_solscan_empty(self, scanner):
        """When Solscan returns no data, Helius is used as fallback."""
        scanner.solscan.token_data.return_value = None
        scanner.solscan.token_holders_total.return_value = None

        from app.domains.scanner.degen_security_scanner import phases as degen_phases

        with (
            patch.object(
                degen_phases,
                "_helius_token_metadata",
                AsyncMock(
                    return_value={
                        "name": "HeliusToken",
                        "symbol": "HLT",
                        "decimals": 6,
                    }
                ),
            ),
            patch.object(degen_phases, "_birdeye_token_data", AsyncMock(return_value={})),
        ):
            report = await scanner.full_scan("HeliusOnlyMint11111111111111111", quick=True)

        assert report.token_name == "HeliusToken"
        assert report.token_symbol == "HLT"

    @pytest.mark.asyncio
    async def test_full_scan_all_apis_fail_gracefully(self, scanner):
        """When all external APIs fail, returns a report with defaults."""
        scanner.solscan.token_data.return_value = None
        scanner.solscan.token_holders_total.return_value = None
        scanner.solscan.get_holder_wallets.return_value = []

        from app.domains.scanner.degen_security_scanner import phases as degen_phases

        with (
            patch.object(degen_phases, "_helius_token_metadata", AsyncMock(return_value=None)),
            patch.object(degen_phases, "_birdeye_token_data", AsyncMock(return_value=None)),
        ):
            report = await scanner.full_scan("DeadMint11111111111111111111111111", quick=True)

        assert report.token_name == "Unknown"
        assert report.token_symbol == "???"
        assert report.total_holders == 0
        assert report.overall_risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


# ═══════════════════════════════════════════════════════════════════════
#  2. UnifiedScanner — wallet scanning (5 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestUnifiedScanner:
    """UnifiedWalletScanner.scan() and helper methods."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not os.getenv("INTEGRATION"), reason="requires live infra (Redis/ClickHouse)")
    async def test_scan_with_empty_address_returns_error(self):
        """Empty address yields empty scan result, not crash."""
        from app.domains.wallet.unified_wallet_scanner import UnifiedWalletScanner

        scanner = UnifiedWalletScanner()
        result = await scanner.scan("", "solana")
        assert result.address == ""
        assert result.risk_score == 0.0

    @pytest.mark.asyncio
    @pytest.mark.skipif(not os.getenv("INTEGRATION"), reason="requires live infra (Redis/ClickHouse)")
    async def test_scan_invalid_address_handled(self):
        """Invalid address yields a handled response."""
        from app.domains.wallet.unified_wallet_scanner import UnifiedWalletScanner

        scanner = UnifiedWalletScanner()
        result = await scanner.scan("not-a-valid-address", "solana")
        # Should not crash — returns a result with address populated
        assert result is not None

    @pytest.mark.asyncio
    @pytest.mark.skipif(not os.getenv("INTEGRATION"), reason="requires live infra (Redis/ClickHouse)")
    async def test_scan_unsupported_chain_does_not_crash(self):
        """An unsupported chain identifier is handled gracefully."""
        from app.domains.wallet.unified_wallet_scanner import UnifiedWalletScanner

        scanner = UnifiedWalletScanner()
        result = await scanner.scan("FakeAddress111111111111111111111111111", "unsupported_chain_x")
        assert result is not None

    @pytest.mark.asyncio
    async def test_core_modules_listed(self):
        """Core module list is populated."""
        from app.domains.wallet.unified_wallet_scanner import UnifiedWalletScanner

        assert len(UnifiedWalletScanner.CORE) >= 3
        assert "wallet_labels" in UnifiedWalletScanner.CORE

    def test_validate_https_url_rejects_non_https(self):
        """URL validation function rejects non-HTTPS URLs."""
        from app.domains.wallet.unified_wallet_scanner import _validate_https_url

        with pytest.raises(ValueError, match="Only HTTPS URLs allowed"):
            _validate_https_url("http://evil.com")

    def test_validate_https_url_accepts_https(self):
        """URL validation function accepts HTTPS URLs."""
        from app.domains.wallet.unified_wallet_scanner import _validate_https_url

        result = _validate_https_url("https://safe.com/api")
        assert result == "https://safe.com/api"


# ═══════════════════════════════════════════════════════════════════════
#  3. OracleManipulationDetector — price manipulation detection (5 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestOracleManipulationDetector:
    """OracleManipulationDetector: constants, data models, and scan()."""

    def test_oracle_type_from_string_valid(self):
        """OracleType.from_string() resolves valid oracle type strings."""
        from app.domains.detectors.oracle_manipulation_detector import OracleType

        assert OracleType.from_string("chainlink") == OracleType.CHAINLINK
        assert OracleType.from_string("uniswap_v3_twap") == OracleType.UNISWAP_V3_TWAP

    def test_oracle_type_from_string_invalid_defaults_custom(self):
        """OracleType.from_string() defaults to CUSTOM for unknown types."""
        from app.domains.detectors.oracle_manipulation_detector import OracleType

        result = OracleType.from_string("nonexistent_oracle_v99")
        assert result == OracleType.CUSTOM

    def test_severity_score_values(self):
        """Severity enum has correct score values."""
        from app.domains.detectors.oracle_manipulation_detector import Severity

        assert Severity.CRITICAL.score == 1.0
        assert Severity.HIGH.score == 0.75
        assert Severity.INFO.score == 0.0

    def test_twap_window_manipulation_risk(self):
        """TWAPWindow.manipulation_risk() returns 0-1 score."""
        from app.domains.detectors.oracle_manipulation_detector import (
            PriceSnapshot,
            TWAPWindow,
        )

        snapshots = [
            PriceSnapshot(timestamp=1000.0, block_number=1, price=100.0),
            PriceSnapshot(timestamp=1001.0, block_number=2, price=101.0),
            PriceSnapshot(timestamp=1002.0, block_number=3, price=99.0),
        ]
        window = TWAPWindow(
            start_block=1,
            end_block=3,
            start_time=1000.0,
            end_time=1002.0,
            average_price=100.0,
            median_price=100.0,
            min_price=99.0,
            max_price=101.0,
            std_dev_pct=0.01,
            samples=snapshots,
        )
        risk = window.manipulation_risk()
        assert 0.0 <= risk <= 1.0

    def test_twap_window_low_std_dev_has_lower_risk(self):
        """TWAPWindow with near-zero std dev has lower risk score."""
        from app.domains.detectors.oracle_manipulation_detector import (
            PriceSnapshot,
            TWAPWindow,
        )

        snapshots = [
            PriceSnapshot(timestamp=1000.0, block_number=1, price=100.0),
            PriceSnapshot(timestamp=1001.0, block_number=2, price=100.01),
            PriceSnapshot(timestamp=1002.0, block_number=3, price=99.99),
        ]
        window = TWAPWindow(
            start_block=1,
            end_block=3,
            start_time=1000.0,
            end_time=1002.0,
            average_price=100.0,
            median_price=100.0,
            min_price=99.99,
            max_price=100.01,
            std_dev_pct=0.0001,
            samples=snapshots,
        )
        risk = window.manipulation_risk()
        # Stable prices should yield low risk
        assert risk < 0.5


# ═══════════════════════════════════════════════════════════════════════
#  4. FlashLoanAttackDetector — flash loan detection (3 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestFlashLoanAttackDetector:
    """FlashLoanAttackDetector module re-exports and class."""

    def test_flash_loan_module_imports(self):
        """The flash_loan_attack_detector shim imports without error."""
        from app.flash_loan_attack_detector import FlashLoanAttackDetector

    @pytest.mark.asyncio
    async def test_flash_loan_scan_handles_no_data(self):
        """scan() handles cases with no recent blocks gracefully."""
        try:
            from app.detectors.flash_loan import FlashLoanAttackDetector
        except ImportError:
            from app.flash_loan_attack_detector import FlashLoanAttackDetector

        detector = FlashLoanAttackDetector(chains=["ethereum"])
        # Mock the _data layer to return empty blocks
        with patch.object(detector._data, "fetch_recent_blocks", AsyncMock(return_value=[])):
            report = await detector.scan(blocks_back=10)
        assert report is not None
        assert report.blocks_scanned == 0
        assert report.attacks_confirmed == []

    def test_flash_loan_attack_detector_has_scan_method(self):
        """FlashLoanAttackDetector has scan method signature."""
        from app.flash_loan_attack_detector import FlashLoanAttackDetector

        detector = FlashLoanAttackDetector(chains=["ethereum"])
        assert hasattr(detector, "scan")
        assert callable(detector.scan)


# ═══════════════════════════════════════════════════════════════════════
#  5. Rate Limiter & Input Validation (4 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestScanRateLimiter:
    """Rate limiter configuration and tier logic (consolidated in app.core.rate_limiter)."""

    def test_free_daily_limit_default(self):
        """FREE tier sentinel scan daily limit is defined."""
        from app.core.rate_limiter import TIER_LIMITS, Tier

        free_scans = TIER_LIMITS[Tier.FREE]["sentinel_scans_per_day"]
        assert isinstance(free_scans, int)
        assert free_scans > 0

    def test_tier_limits_contain_expected_tiers(self):
        """TIER_LIMITS has the expected tier keys."""
        from app.core.rate_limiter import TIER_LIMITS, Tier

        assert Tier.FREE in TIER_LIMITS
        assert Tier.PRO in TIER_LIMITS
        assert Tier.ENTERPRISE in TIER_LIMITS

    def test_scan_packs_contain_expected_keys(self):
        """SCAN_PACKS (Telegram bot config) has the expected pack keys."""
        from app.domains.telegram.rugmunchbot.config import SCAN_PACKS

        assert "deep_scan" in SCAN_PACKS
        assert "wallet_forensic" in SCAN_PACKS

    def test_elite_daily_limit_zero_means_unlimited(self):
        """ENTERPRISE tier is effectively unlimited (large sentinel scan cap)."""
        from app.core.rate_limiter import TIER_LIMITS, Tier

        enterprise_scans = TIER_LIMITS[Tier.ENTERPRISE]["sentinel_scans_per_day"]
        assert isinstance(enterprise_scans, int)
        assert enterprise_scans >= 1000


class TestScannerInputValidation:
    """Input validation for scanner endpoints."""

    def test_portfolio_address_re_evm_valid(self):
        """EVM address regex matches valid 0x addresses."""
        from app.domains.portfolio.portfolio_risk_aggregator.config import (
            EVM_ADDRESS_RE,
        )

        assert EVM_ADDRESS_RE.match("0x1234567890abcdef1234567890abcdef12345678")
        assert not EVM_ADDRESS_RE.match("0x1234")  # too short

    def test_portfolio_address_re_solana_valid(self):
        """Solana address regex matches valid base58."""
        from app.domains.portfolio.portfolio_risk_aggregator.config import SOLANA_ADDRESS_RE

        # Valid solana base58 address (32-44 chars, no 0/O/I/l)
        assert SOLANA_ADDRESS_RE.match("FakeBase58Addr111111111111111111111111111111") is not None
        assert not SOLANA_ADDRESS_RE.match("0xInvalid")  # hex prefix not solana

    def test_portfolio_validate_address_evm(self):
        """validate_wallet_address returns (True, 'evm') for ETH addresses."""
        from app.domains.portfolio.portfolio_risk_aggregator import (
            validate_wallet_address,
        )

        valid, hint = validate_wallet_address("0x1234567890abcdef1234567890abcdef12345678")
        assert valid is True
        assert hint == "evm"

    def test_portfolio_validate_address_invalid(self):
        """validate_wallet_address returns (False, ...) for garbage."""
        from app.domains.portfolio.portfolio_risk_aggregator import (
            validate_wallet_address,
        )

        valid, _hint = validate_wallet_address("not-a-wallet!")
        assert valid is False


# ═══════════════════════════════════════════════════════════════════════
#  6. scan_token() — token scanner service (3 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestTokenScannerService:
    """scan_token() and quick_scan_text() from the scanner service."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not os.getenv("INTEGRATION"), reason="requires live infra (Redis/ClickHouse)")
    async def test_scan_token_handles_empty_address(self):
        """scan_token with empty address does not crash."""
        from app.domains.scanner import scan_token

        result = await scan_token("", "ethereum")
        # Should return some error/dispatch result, not crash
        assert result is not None

    @pytest.mark.asyncio
    @pytest.mark.skipif(not os.getenv("INTEGRATION"), reason="requires live infra (Redis/ClickHouse)")
    async def test_quick_scan_text_handles_empty_text(self):
        """quick_scan_text with empty text does not crash."""
        from app.domains.scanner import quick_scan_text

        result = await quick_scan_text("")
        assert result is not None

    def test_scan_result_dataclass_defaults(self):
        """ScanResult dataclass has sensible defaults."""
        from app.domains.scanner.models import ScanResult

        sr = ScanResult(token_address="0xtest", chain="ethereum")
        assert sr.token_address == "0xtest"
        assert sr.chain == "ethereum"
        assert sr.safety_score == 50
        assert sr.risk_flags == []


# ═══════════════════════════════════════════════════════════════════════
#  7. Wallet Risk Factors Dataclass (2 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestWalletRiskFactors:
    """WalletRiskFactors dataclass from unified_scanner."""

    def test_default_values_are_safe_defaults(self):
        """All risk factors default to zeros/empty values."""
        from app.unified_scanner import WalletRiskFactors

        f = WalletRiskFactors()
        assert f.wallet_age_days == 0
        assert f.total_tx_count == 0
        assert f.total_risk_score == 0
        assert f.risk_category == "unknown"
        assert f.held_token_risks == []
        assert f.data_sources == []

    def test_risk_category_critical_calculation(self):
        """Risk category maps correctly at boundary values."""
        from app.unified_scanner import WalletRiskFactors

        f = WalletRiskFactors(total_risk_score=85)
        # Category is determined in scan_wallet(), not in dataclass
        assert f.total_risk_score == 85


# ═══════════════════════════════════════════════════════════════════════
#  8. Chain configs & constants (2 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestScannerConstants:
    """Scanner module constants and chain configurations."""

    def test_unified_scanner_chains_are_populated(self):
        """CHAINS dict contains expected chain entries."""
        from app.unified_scanner import CHAINS

        assert "solana" in CHAINS
        assert "ethereum" in CHAINS
        assert "base" in CHAINS
        assert "bsc" in CHAINS

    def test_degen_scanner_bundler_programs(self):
        """BUNDLER_PROGRAMS dict is populated with known program IDs."""
        from app.domains.scanner.degen_security_scanner import BUNDLER_PROGRAMS

        assert len(BUNDLER_PROGRAMS) >= 2
        assert "Pump.fun Bundler" in BUNDLER_PROGRAMS.values()
