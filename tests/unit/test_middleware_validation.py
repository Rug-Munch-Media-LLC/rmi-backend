"""Unit tests for app.middleware.validation — Pydantic request models and validators.

Tests cover:
- WalletRequest: address validation, chain validation, case normalization
- BatchWalletRequest: multiple addresses, min/max length
- ContractRequest: deep scan flag
- TextSearchRequest: query and limit validation
- ThreatCheckRequest: flags
- PortfolioRequest: history_days range
- MempoolMonitorRequest: gas threshold
- Utility validators: tier_validator, validate_x402_payment
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.middleware.validation import (
    ADDRESS_PATTERN,
    CHAIN_NAME_PATTERN,
    HASH_PATTERN,
    WALLET_PATTERN,
    BatchWalletRequest,
    ContractRequest,
    MempoolMonitorRequest,
    PortfolioRequest,
    TextSearchRequest,
    ThreatCheckRequest,
    WalletRequest,
    tier_validator,
    validate_x402_payment,
)

# ── Regex pattern tests ────────────────────────────────────────


class TestPatternCompilation:
    """Verify that compiled regex patterns match expected formats."""

    @pytest.mark.parametrize(
        "address, expected",
        [
            ("0x" + "a" * 40, True),
            ("0x" + "A" * 40, True),
            ("0x" + "1" * 40, True),
            ("0x" + "a" * 38, True),
            ("0x" + "b" * 64, True),
            ("0x" + "c" * 65, False),  # too long
            ("0x" + "d" * 37, False),  # too short
            ("nothex", False),
            ("0xyz1234", False),
        ],
    )
    def test_address_pattern(self, address: str, expected: bool) -> None:
        assert bool(ADDRESS_PATTERN.match(address)) == expected

    @pytest.mark.parametrize(
        "hash_str, expected",
        [
            ("0x" + "a" * 64, True),
            ("0x" + "0" * 64, True),
            ("0x" + "a" * 63, False),
            ("0x" + "a" * 65, False),
            ("abc123", False),
            ("0x" + "g" * 64, False),  # 'g' is not hex
        ],
    )
    def test_hash_pattern(self, hash_str: str, expected: bool) -> None:
        assert bool(HASH_PATTERN.match(hash_str)) == expected

    def test_wallet_pattern_matches_address(self) -> None:
        """WALLET_PATTERN should have same constraints as ADDRESS_PATTERN."""
        assert WALLET_PATTERN.pattern == ADDRESS_PATTERN.pattern

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("ethereum", True),
            ("base", True),
            ("arbitrum", True),
            ("polygon-zkevm", True),
            ("Ethereum", False),  # not lowercase
            ("123invalid", False),  # starts with digit
            ("a", True),
            ("ab", True),
            ("a" * 20, True),
            ("", False),
        ],
    )
    def test_chain_name_pattern(self, name: str, expected: bool) -> None:
        assert bool(CHAIN_NAME_PATTERN.match(name)) == expected


# ── WalletRequest ──────────────────────────────────────────────


class TestWalletRequest:
    """Pydantic model for single wallet address requests."""

    def test_valid_ethereum_address(self) -> None:
        req = WalletRequest(address="0x" + "a" * 40, chain="ethereum")
        assert req.address == "0x" + "a" * 40
        assert req.chain == "ethereum"

    def test_address_without_0x_prefix_adds_it(self) -> None:
        """When address lacks 0x prefix, the validator prepends it."""
        req = WalletRequest(address="a" * 40, chain="ethereum")
        assert req.address.startswith("0x")
        assert len(req.address) == 42

    def test_address_normalized_to_lowercase(self) -> None:
        """Mixed-case addresses are lowercased by the validator."""
        req = WalletRequest(address="0x" + "AbCdEf1234" + "0" * 30, chain="ethereum")
        assert req.address == ("0x" + "abcdef1234" + "0" * 30).lower()

    def test_chain_normalized_to_lowercase(self) -> None:
        req = WalletRequest(address="0x" + "a" * 40, chain="ETHEREUM")
        assert req.chain == "ethereum"

    def test_unknown_chain_allows_with_warning(self) -> None:
        """Unknown chains should be permitted (warning logged, not error)."""
        req = WalletRequest(address="0x" + "a" * 40, chain="unknown-chain-xyz")
        assert req.chain == "unknown-chain-xyz"

    def test_address_too_short_raises(self) -> None:
        with pytest.raises(ValidationError):
            WalletRequest(address="0x1234", chain="ethereum")

    def test_address_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            WalletRequest(address="0x" + "a" * 100, chain="ethereum")

    def test_address_invalid_hex_raises(self) -> None:
        with pytest.raises(ValidationError):
            WalletRequest(address="0xZZZZZZZZ" + "a" * 32, chain="ethereum")

    def test_empty_address_raises(self) -> None:
        with pytest.raises(ValidationError):
            WalletRequest(address="", chain="ethereum")

    def test_default_chain_is_ethereum(self) -> None:
        req = WalletRequest(address="0x" + "a" * 40)
        assert req.chain == "ethereum"


# ── BatchWalletRequest ─────────────────────────────────────────


class TestBatchWalletRequest:
    """Pydantic model for batch wallet operations."""

    def test_valid_batch(self) -> None:
        addrs = ["0x" + "a" * 40, "0x" + "b" * 40]
        req = BatchWalletRequest(addresses=addrs)
        assert len(req.addresses) == 2
        assert req.chain == "ethereum"

    def test_addresses_normalized(self) -> None:
        addrs = ["0xABCD" + "0" * 36, "b" * 40]
        req = BatchWalletRequest(addresses=addrs)
        assert req.addresses[0] == ("0xabcd" + "0" * 36).lower()
        assert req.addresses[1] == "0x" + "b" * 40

    def test_empty_batch_raises(self) -> None:
        with pytest.raises(ValidationError):
            BatchWalletRequest(addresses=[])

    def test_too_many_addresses_raises(self) -> None:
        with pytest.raises(ValidationError):
            BatchWalletRequest(addresses=["0x" + "a" * 40] * 51)

    def test_invalid_address_in_batch_raises(self) -> None:
        with pytest.raises(ValidationError):
            BatchWalletRequest(addresses=["0x" + "a" * 40, "not-an-address"])


# ── ContractRequest ────────────────────────────────────────────


class TestContractRequest:
    """Extends WalletRequest with deep scan flag."""

    def test_default_deep_is_false(self) -> None:
        req = ContractRequest(address="0x" + "a" * 40)
        assert req.deep is False

    def test_deep_true(self) -> None:
        req = ContractRequest(address="0x" + "a" * 40, deep=True)
        assert req.deep is True

    def test_inherits_address_validation(self) -> None:
        with pytest.raises(ValidationError):
            ContractRequest(address="invalid")

    def test_inherits_chain_default(self) -> None:
        req = ContractRequest(address="0x" + "a" * 40)
        assert req.chain == "ethereum"


# ── TextSearchRequest ──────────────────────────────────────────


class TestTextSearchRequest:
    """Search query with limit."""

    def test_valid_search(self) -> None:
        req = TextSearchRequest(query="rug pull detection")
        assert req.query == "rug pull detection"
        assert req.limit == 10

    def test_custom_limit(self) -> None:
        req = TextSearchRequest(query="test", limit=50)
        assert req.limit == 50

    def test_empty_query_raises(self) -> None:
        with pytest.raises(ValidationError):
            TextSearchRequest(query="")

    def test_query_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            TextSearchRequest(query="x" * 201)

    def test_limit_below_min_raises(self) -> None:
        with pytest.raises(ValidationError):
            TextSearchRequest(query="test", limit=0)

    def test_limit_above_max_raises(self) -> None:
        with pytest.raises(ValidationError):
            TextSearchRequest(query="test", limit=101)


# ── ThreatCheckRequest ─────────────────────────────────────────


class TestThreatCheckRequest:
    """Extends WalletRequest with threat check flags."""

    def test_default_checks_enabled(self) -> None:
        req = ThreatCheckRequest(address="0x" + "a" * 40)
        assert req.check_sanctions is True
        assert req.check_risk is True
        assert req.check_exchange is True

    def test_disable_checks(self) -> None:
        req = ThreatCheckRequest(
            address="0x" + "a" * 40,
            check_sanctions=False,
            check_risk=False,
            check_exchange=False,
        )
        assert req.check_sanctions is False
        assert req.check_risk is False
        assert req.check_exchange is False

    def test_inherits_address_validation(self) -> None:
        with pytest.raises(ValidationError):
            ThreatCheckRequest(address="invalid")


# ── MempoolMonitorRequest ──────────────────────────────────────


class TestMempoolMonitorRequest:
    """Gas threshold and bot alert settings."""

    def test_default_values(self) -> None:
        req = MempoolMonitorRequest(address="0x" + "a" * 40)
        assert req.threshold_gas_gwei == 50.0
        assert req.alert_on_bot is True

    def test_custom_gas_threshold(self) -> None:
        req = MempoolMonitorRequest(address="0x" + "a" * 40, threshold_gas_gwei=100.0)
        assert req.threshold_gas_gwei == 100.0

    def test_gas_below_min_raises(self) -> None:
        with pytest.raises(ValidationError):
            MempoolMonitorRequest(address="0x" + "a" * 40, threshold_gas_gwei=0.5)

    def test_gas_above_max_raises(self) -> None:
        with pytest.raises(ValidationError):
            MempoolMonitorRequest(address="0x" + "a" * 40, threshold_gas_gwei=2000.0)


# ── PortfolioRequest ───────────────────────────────────────────


class TestPortfolioRequest:
    """Portfolio analysis parameters."""

    def test_default_values(self) -> None:
        req = PortfolioRequest(address="0x" + "a" * 40)
        assert req.include_tokens is True
        assert req.include_nfts is False
        assert req.history_days == 30

    def test_custom_history(self) -> None:
        req = PortfolioRequest(address="0x" + "a" * 40, history_days=90, include_nfts=True)
        assert req.history_days == 90
        assert req.include_nfts is True

    def test_history_below_min_raises(self) -> None:
        with pytest.raises(ValidationError):
            PortfolioRequest(address="0x" + "a" * 40, history_days=0)

    def test_history_above_max_raises(self) -> None:
        with pytest.raises(ValidationError):
            PortfolioRequest(address="0x" + "a" * 40, history_days=366)


# ── Utility validators ─────────────────────────────────────────


class TestTierValidator:
    """tier_validator() helper function."""

    def test_free_tier(self) -> None:
        limits = tier_validator("FREE")
        assert limits["requests_per_hour"] == 10
        assert limits["requests_per_day"] == 100

    def test_basic_tier(self) -> None:
        limits = tier_validator("BASIC")
        assert limits["requests_per_hour"] == 50

    def test_premium_tier(self) -> None:
        limits = tier_validator("PREMIUM")
        assert limits["requests_per_hour"] == 200

    def test_enterprise_tier(self) -> None:
        limits = tier_validator("ENTERPRISE")
        assert limits["requests_per_hour"] == 1000

    def test_case_insensitive(self) -> None:
        assert tier_validator("free")["requests_per_hour"] == 10
        assert tier_validator("Premium")["requests_per_hour"] == 200

    def test_unknown_tier_falls_back_to_free(self) -> None:
        limits = tier_validator("UNKNOWN")
        assert limits == tier_validator("FREE")

    def test_empty_tier_falls_back(self) -> None:
        limits = tier_validator("")
        assert limits["requests_per_hour"] == 10


class TestValidateX402Payment:
    """validate_x402_payment() helper function."""

    @pytest.mark.parametrize(
        "tier, required, expected",
        [
            ("FREE", 5, True),
            ("FREE", 10, True),
            ("FREE", 11, False),
            ("BASIC", 50, True),
            ("BASIC", 100, True),
            ("BASIC", 200, False),
            ("PREMIUM", 500, True),
            ("PREMIUM", 1000, True),
            ("PREMIUM", 2000, False),
            ("ENTERPRISE", 5000, True),
            ("ENTERPRISE", 10000, True),
            ("ENTERPRISE", 20000, False),
        ],
    )
    def test_capability_check(self, tier: str, required: int, expected: bool) -> None:
        assert validate_x402_payment(required, tier) is expected

    def test_case_insensitive(self) -> None:
        assert validate_x402_payment(5, "free") is True
        assert validate_x402_payment(11, "Free") is False

    def test_unknown_tier_returns_false(self) -> None:
        assert validate_x402_payment(5, "UNKNOWN") is False

    def test_zero_required_always_true(self) -> None:
        assert validate_x402_payment(0, "FREE") is True
        assert validate_x402_payment(0, "UNKNOWN") is True


# ── Constants ──────────────────────────────────────────────────


class TestValidationConstants:
    """Module-level constants."""

    def test_valid_chains_contains_ethereum(self) -> None:
        from app.middleware.validation import VALID_CHAINS

        assert "ethereum" in VALID_CHAINS

    def test_valid_chains_is_set(self) -> None:
        from app.middleware.validation import VALID_CHAINS

        assert isinstance(VALID_CHAINS, set)
        assert len(VALID_CHAINS) >= 15  # growing list

    def test_length_constants_valid(self) -> None:
        from app.middleware.validation import (
            MAX_ADDRESS_LENGTH,
            MAX_CHAIN_LENGTH,
            MIN_ADDRESS_LENGTH,
            MIN_CHAIN_LENGTH,
        )

        assert 38 <= MIN_ADDRESS_LENGTH <= MAX_ADDRESS_LENGTH
        assert MAX_ADDRESS_LENGTH == 66
        assert MIN_CHAIN_LENGTH == 2
        assert MAX_CHAIN_LENGTH == 20
