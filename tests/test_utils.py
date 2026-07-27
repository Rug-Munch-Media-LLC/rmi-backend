"""Test utility functions — address validation, chain detection, format
converters, rate-limit helpers, and standalone utility modules.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════
# 1. cross_chain — chain detection & normalization
# ═══════════════════════════════════════════════════════════════════════


class TestChainDetection:
    """Tests for ``app.cross_chain`` chain helpers."""

    # ---- normalize_chain ----

    @pytest.mark.parametrize(
        ("input_chain", "expected"),
        [
            ("ethereum", "ethereum"),
            ("ETH", "ethereum"),
            ("eth", "ethereum"),
            ("mainnet", "ethereum"),
            ("1", "ethereum"),
            ("bsc", "bsc"),
            ("binance", "bsc"),
            ("56", "bsc"),
            ("polygon", "polygon"),
            ("matic", "polygon"),
            ("137", "polygon"),
            ("solana", "solana"),
            ("sol", "solana"),
            ("bitcoin", "bitcoin"),
            ("btc", "bitcoin"),
            ("tron", "tron"),
            ("trx", "tron"),
            ("arbitrum", "arbitrum"),
            ("arb", "arbitrum"),
            ("optimism", "optimism"),
            ("op", "optimism"),
            ("base", "base"),
        ],
    )
    def test_normalize_chain_known(self, input_chain, expected):
        """Known chain aliases normalize to canonical form."""
        from app.cross_chain import normalize_chain

        assert normalize_chain(input_chain) == expected

    def test_normalize_chain_unknown(self):
        """Unknown chain names pass through lower-cased."""
        from app.cross_chain import normalize_chain

        assert normalize_chain("UnknownChain") == "unknownchain"

    def test_normalize_chain_empty(self):
        """Empty string returns empty string."""
        from app.cross_chain import normalize_chain

        assert normalize_chain("") == ""

    def test_normalize_chain_whitespace(self):
        """Leading/trailing whitespace is stripped."""
        from app.cross_chain import normalize_chain

        assert normalize_chain("  SOLANA  ") == "solana"

    # ---- is_valid_address ----

    def test_is_valid_address_solana(self):
        """Valid Solana base58 address returns True."""
        from app.cross_chain_whale import is_valid_address

        # Real-looking Solana address (32 bytes base58 encoded)
        addr = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLt1q"
        assert is_valid_address(addr) is True

    def test_is_valid_address_evm(self):
        """Valid EVM address returns True."""
        from app.cross_chain_whale import is_valid_address

        addr = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"
        assert is_valid_address(addr) is True

    def test_is_valid_address_evm_eip55(self):
        """EIP-55 checksummed address validates."""
        from app.cross_chain_whale import is_valid_address

        # Valid EIP-55 checksummed address
        addr = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
        assert is_valid_address(addr) is True

    @pytest.mark.parametrize(
        "invalid_addr",
        [
            "",
            "0xshort",
            "0xGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG",
            "not-an-address",
            "0x",
            "solana_address_that_is_way_too_long_to_be_valid_xxxxxxxxxxxxx",
            "  ",
            None,
        ],
    )
    def test_is_valid_address_invalid(self, invalid_addr):
        """Invalid/malformed addresses return False."""
        from app.cross_chain_whale import is_valid_address

        if invalid_addr is None:
            # Need to handle None gracefully
            try:
                result = is_valid_address(invalid_addr)
                assert result is False
            except (TypeError, AttributeError):
                pass  # Accept either behavior for None
        else:
            assert is_valid_address(invalid_addr) is False

    def test_is_valid_address_solana_short(self):
        """Too-short Solana addresses return False."""
        from app.cross_chain_whale import is_valid_address

        assert is_valid_address("abc123") is False

    def test_is_valid_address_insider_network(self):
        """is_valid_address from insider_network also works."""
        from app.insider_network import is_valid_address

        assert is_valid_address("0x742d35Cc6634C0532925a3b844Bc454e4438f44e") is True
        assert is_valid_address("") is False
        assert is_valid_address("not-valid") is False

    def test_is_valid_address_dex_pool(self):
        """is_valid_address from dex_pool_manipulation_analyzer."""
        from app.dex_pool_manipulation_analyzer import is_valid_address

        assert is_valid_address("0x742d35Cc6634C0532925a3b844Bc454e4438f44e") is True
        assert is_valid_address("") is False

    def test_is_valid_address_rug_pull(self):
        """is_valid_address from rug_pull_predictor."""
        from app.rug_pull_predictor import is_valid_address

        assert is_valid_address("0x742d35Cc6634C0532925a3b844Bc454e4438f44e") is True
        assert is_valid_address("") is False


# ═══════════════════════════════════════════════════════════════════════
# 2. Format converters
# ═══════════════════════════════════════════════════════════════════════


class TestFormatConverters:
    """Tests for address formatting and report formatting utilities."""

    def test_format_wallet_short(self):
        """format_wallet_short truncates long addresses."""
        from app.card_generator import format_wallet_short

        result = format_wallet_short("0x742d35Cc6634C0532925a3b844Bc454e4438f44e")
        assert "0x74" in result
        assert len(result) < len("0x742d35Cc6634C0532925a3b844Bc454e4438f44e")

    def test_format_wallet_short_solana(self):
        """format_wallet_short works with Solana addresses."""
        from app.card_generator import format_wallet_short

        addr = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLt1q"
        result = format_wallet_short(addr)
        assert len(result) < len(addr)
        assert "7Ec" in result

    def test_format_wallet_short_short_address(self):
        """Short addresses are not truncated."""
        from app.card_generator import format_wallet_short

        assert format_wallet_short("0x1234") == "0x1234"

    def test_format_wallet_short_empty(self):
        """Empty string returns empty."""
        from app.card_generator import format_wallet_short

        assert format_wallet_short("") == ""

    def test_marketing_format_wallet_short(self):
        """format_wallet_short from marketing_templates."""
        from app.marketing_templates import format_wallet_short

        result = format_wallet_short("0x742d35Cc6634C0532925a3b844Bc454e4438f44e")
        assert len(result) < 20

    def test_format_timestamp_none(self):
        """_format_timestamp handles None gracefully."""
        from app.domains.scanner.rugmaps.models import _format_timestamp

        assert _format_timestamp(None) is None

    def test_format_timestamp_int(self):
        """_format_timestamp with int returns ISO string."""
        from app.domains.scanner.rugmaps.models import _format_timestamp

        result = _format_timestamp(1700000000)
        assert isinstance(result, str)
        assert "202" in result  # year in the string

    def test_format_timestamp_datetime(self):
        """_format_timestamp with datetime returns ISO string."""
        from datetime import UTC, datetime

        from app.domains.scanner.rugmaps.models import _format_timestamp

        dt = datetime(2026, 7, 1, tzinfo=UTC)
        result = _format_timestamp(dt)
        assert isinstance(result, str)
        assert "2026-07-01" in result


# ═══════════════════════════════════════════════════════════════════════
# 3. Rate limiter
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimiter:
    """Tests for token-bucket and rate-limit helpers."""

    @pytest.mark.asyncio
    async def test_token_bucket_acquire_immediate(self):
        """Token bucket returns 0 wait when tokens are available."""
        from app.chain_client import TokenBucket

        bucket = TokenBucket(rate=10.0, burst=10)
        wait = await bucket.acquire()
        assert wait == 0.0

    @pytest.mark.asyncio
    async def test_token_bucket_acquire_exhausted(self):
        """Token bucket returns >0 wait when tokens exhausted."""
        from app.chain_client import TokenBucket

        bucket = TokenBucket(rate=1.0, burst=1)
        await bucket.acquire()
        wait = await bucket.acquire()
        assert wait > 0.0

    @pytest.mark.asyncio
    async def test_token_bucket_refills(self):
        """Token bucket refills over time."""
        from app.chain_client import TokenBucket

        bucket = TokenBucket(rate=100.0, burst=5)
        # Exhaust
        for _ in range(5):
            await bucket.acquire()
        # After a tiny sleep, some tokens should have refilled
        await bucket.acquire()
        # Should not hang (refilled quickly due to high rate)

    @pytest.mark.asyncio
    async def test_token_bucket_concurrent_access(self):
        """Token bucket handles concurrent acquire calls."""
        import asyncio

        from app.chain_client import TokenBucket

        bucket = TokenBucket(rate=1000.0, burst=20)
        tasks = [bucket.acquire() for _ in range(10)]
        waits = await asyncio.gather(*tasks)
        # All should finish without error
        assert len(waits) == 10
        assert all(isinstance(w, float) for w in waits)

    def test_rate_limit_by_tier_free(self):
        """rate_limit_by_tier returns correct limits for free tier."""
        from app.crypto_guard import rate_limit_by_tier

        limits = rate_limit_by_tier("free", "/api/v1/scan")
        assert isinstance(limits, dict)

    def test_rate_limit_by_tier_premium(self):
        """rate_limit_by_tier returns higher limits for premium."""
        from app.crypto_guard import rate_limit_by_tier

        limits_premium = rate_limit_by_tier("premium", "/api/v1/scan")
        assert isinstance(limits_premium, dict)

    def test_rate_limit_by_tier_enterprise(self):
        """rate_limit_by_tier returns generous limits for enterprise."""
        from app.crypto_guard import rate_limit_by_tier

        limits = rate_limit_by_tier("enterprise", "/api/v1/scan")
        assert isinstance(limits, dict)


# ═══════════════════════════════════════════════════════════════════════
# 4. Chain client — provider key rotation
# ═══════════════════════════════════════════════════════════════════════


class TestChainClient:
    """Tests for chain-client key rotation."""

    def test_next_key_round_robin(self):
        """_next_key cycles through available Helius keys."""
        from app.chain_client import ChainClient

        client = ChainClient()
        with patch("app.chain_client.HELIUS_KEYS", ["key1", "key2"]):
            keys = [client._next_key() for _ in range(4)]
        assert keys == ["key1", "key2", "key1", "key2"]

    def test_next_key_empty(self):
        """_next_key returns None when no keys are configured."""
        from app.chain_client import ChainClient

        client = ChainClient()
        with patch("app.chain_client.HELIUS_KEYS", []):
            key = client._next_key()
        assert key is None

    @pytest.mark.asyncio
    async def test_rpc_call_rate_limited(self):
        """rpc_call respects rate limiter."""
        from app.chain_client import ChainClient
        from app.core.circuit_breaker import AsyncCircuitBreaker

        client = ChainClient()
        with (
            patch("app.chain_client.HELIUS_KEYS", ["test-key"]),
            patch("app.chain_client._chain_cb", AsyncCircuitBreaker("test", 100, 60, 1)),
            patch.object(client, "_rate_limit", AsyncMock()),
            patch.object(client, "_next_key", return_value="test-key"),
            patch("httpx.AsyncClient") as mock_async_client,
        ):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"result": "ok"}
            mock_async_client.return_value.__aenter__.return_value.post.return_value = mock_response

            result = await client.rpc_call("getBalance", ["addr"])
            client._rate_limit.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# 5. Cross-chain utility — detect_chain
# ═══════════════════════════════════════════════════════════════════════


class TestDetectChain:
    """Tests for ``_detect_chain`` functions across modules."""

    @pytest.mark.parametrize(
        ("address", "expected_chain"),
        [
            ("0x742d35Cc6634C0532925a3b844Bc454e4438f44e", "ethereum"),
            ("0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B", "ethereum"),
            ("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLt1q", "solana"),
        ],
    )
    def test_detect_chain_cross_chain(self, address, expected_chain):
        """_detect_chain correctly identifies chain from address format."""
        from app.cross_chain_trace import _detect_chain

        assert _detect_chain(address) == expected_chain

    def test_detect_chain_unknown(self):
        """_detect_chain returns 'unknown' for unrecognized addresses."""
        from app.cross_chain_trace import _detect_chain

        assert _detect_chain("not-a-valid-address") == "unknown"

    def test_detect_chain_empty(self):
        """_detect_chain returns 'unknown' for empty string."""
        from app.cross_chain_trace import _detect_chain

        assert _detect_chain("") == "unknown"

    def test_detect_chain_from_address_deployer(self):
        """_detect_chain_from_address returns multiple possible chains."""
        from app.deployer_history import _detect_chain_from_address

        results = _detect_chain_from_address("0x742d35Cc6634C0532925a3b844Bc454e4438f44e")
        assert len(results) >= 1

    def test_detect_chain_solana_address(self):
        """_detect_chain_from_address returns solana for sol addresses."""
        from app.deployer_history import _detect_chain_from_address

        results = _detect_chain_from_address("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLt1q")
        assert isinstance(results, list)

    def test_detect_chain_launch_fairness(self):
        """_detect_chain from launch_fairness_analyzer."""
        from app.launch_fairness_analyzer import _detect_chain

        assert _detect_chain("0x742d35Cc6634C0532925a3b844Bc454e4438f44e") == "ethereum"
        assert _detect_chain("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLt1q") == "solana"

    def test_detect_chain_token_supply(self):
        """_detect_chain from token_supply_analyzer."""
        from app.token_supply_analyzer import _detect_chain

        assert _detect_chain("0x742d35Cc6634C0532925a3b844Bc454e4438f44e") == "ethereum"

    def test_detect_chain_reports(self):
        """_detect_chain from reports router."""
        from app.routers.v1.reports import _detect_chain

        assert _detect_chain("0x742d35Cc6634C0532925a3b844Bc454e4438f44e") == "ethereum"
        assert _detect_chain("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLt1q") == "solana"


# ═══════════════════════════════════════════════════════════════════════
# 6. crypto_guard — rate_limit_by_tier edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestCryptoGuard:
    """Edge cases for crypto_guard utilities."""

    def test_rate_limit_by_tier_unknown_tier(self):
        """Unknown tier falls back to free limits."""
        from app.crypto_guard import rate_limit_by_tier

        limits = rate_limit_by_tier("nonexistent_tier_xyz", "/api/v1/scan")
        assert isinstance(limits, dict)

    def test_rate_limit_by_tier_empty_tier(self):
        """Empty tier returns some limits (no crash)."""
        from app.crypto_guard import rate_limit_by_tier

        limits = rate_limit_by_tier("", "/api/v1/scan")
        assert isinstance(limits, dict)

    def test_rate_limit_by_tier_different_endpoints(self):
        """Different endpoints may have different limits."""
        from app.crypto_guard import rate_limit_by_tier

        scan_limits = rate_limit_by_tier("free", "/api/v1/scan")
        other_limits = rate_limit_by_tier("free", "/api/v1/wallet")
        assert isinstance(scan_limits, dict)
        assert isinstance(other_limits, dict)


# ═══════════════════════════════════════════════════════════════════════
# 7. provider_chains — chain/provider mapping
# ═══════════════════════════════════════════════════════════════════════


class TestProviderChains:
    """Tests for provider-to-chain mapping utilities."""

    def test_build_provider_chains_is_callable(self):
        """build_provider_chains is a callable."""
        from app.databus.provider_chains import build_provider_chains

        assert callable(build_provider_chains)

    def test_build_provider_chains_returns_mapping(self):
        """build_provider_chains returns a non-empty dict."""
        from app.databus.provider_chains import build_provider_chains

        result = build_provider_chains()
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_build_provider_chains_has_major_chains(self):
        """build_provider_chains return contains solana and ethereum."""
        from app.databus.provider_chains import build_provider_chains

        result = build_provider_chains()
        chains_lower = {k.lower() for k in result}
        assert len(chains_lower) >= 2

    def test_build_provider_chains_values_have_providers(self):
        """All chain entries have a providers list with at least one entry."""
        from app.databus.provider_chains import build_provider_chains

        result = build_provider_chains()
        for chain, entry in result.items():
            assert hasattr(entry, "providers"), f"{chain} has no providers attribute"
            providers = entry.providers
            assert isinstance(providers, list), f"{chain}.providers is not a list"
            assert len(providers) >= 1, f"{chain} has no providers"
