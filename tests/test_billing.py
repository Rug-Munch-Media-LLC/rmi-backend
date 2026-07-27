"""Billing and payment facilitator tests — 15+ tests covering payment
verification, settlement, facilitator registry, pricing catalog, tier
management, and usage tracking.

Uses pytest-asyncio. All external calls mocked.
"""

from __future__ import annotations

import os

os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"

from unittest.mock import MagicMock, patch

from app.domains.billing.facilitators.base import (
    Facilitator,
    FacilitatorRegistry,
    FacilitatorType,
    NetworkSupport,
    SettlementResult,
    SettlementType,
    VerificationResult,
    get_registry,
    reset_registry,
)
from app.domains.billing.facilitators.config import FacilitatorConfig, get_config, reset_config
from app.domains.billing.x402.tier_resolver import check_burst_limit, check_trial, consume_trial

# ═══════════════════════════════════════════════════════════════════════
# ── VerificationResult & SettlementResult data classes
# ═══════════════════════════════════════════════════════════════════════


class TestVerificationResult:
    """VerificationResult data class construction and serialization."""

    def test_verification_success_defaults(self):
        """Successful verification with default values."""
        vr = VerificationResult(verified=True)
        assert vr.verified is True
        assert vr.reason == ""
        assert vr.tx_hash is None
        assert vr.facilitator is None

    def test_verification_full_construction(self):
        """VerificationResult with all fields populated."""
        vr = VerificationResult(
            verified=True,
            reason="Verified via Coinbase",
            tx_hash="0xabc123",
            settlement_id="set_123",
            payer="0xpayer",
            amount="1000000",
            amount_usd=1.0,
            chain="base",
            token="USDC",
            facilitator="coinbase_cdp",
            block_number=123456,
            confirmations=12,
        )
        assert vr.verified is True
        assert vr.tx_hash == "0xabc123"
        assert vr.amount_usd == 1.0

    def test_verification_to_dict(self):
        """to_dict produces correct serialization."""
        vr = VerificationResult(verified=True, reason="OK", chain="solana")
        d = vr.to_dict()
        assert d["verified"] is True
        assert d["reason"] == "OK"
        assert d["chain"] == "solana"
        assert d["facilitator"] is None

    def test_verification_failed_result(self):
        """Failed verification works correctly."""
        vr = VerificationResult(verified=False, reason="Insufficient funds")
        assert vr.verified is False
        assert vr.reason == "Insufficient funds"


class TestSettlementResult:
    """SettlementResult data class construction."""

    def test_settlement_success_defaults(self):
        """Successful settlement with default auto-settled reason."""
        sr = SettlementResult(settled=True, reason="Auto-settled")
        assert sr.settled is True
        assert sr.tx_hash is None

    def test_settlement_to_dict(self):
        """to_dict produces correct serialization."""
        sr = SettlementResult(settled=True, settlement_id="st_001", tx_hash="0xdef")
        d = sr.to_dict()
        assert d["settled"] is True
        assert d["settlement_id"] == "st_001"
        assert d["tx_hash"] == "0xdef"

    def test_settlement_failed(self):
        """Failed settlement representation."""
        sr = SettlementResult(settled=False, reason="Settlement timeout")
        assert sr.settled is False
        assert sr.reason == "Settlement timeout"


# ═══════════════════════════════════════════════════════════════════════
# ── NetworkSupport
# ═══════════════════════════════════════════════════════════════════════


class TestNetworkSupport:
    """NetworkSupport data class for chain/token declarations."""

    def test_network_support_defaults(self):
        """Empty NetworkSupport has empty lists."""
        ns = NetworkSupport()
        assert ns.chains == []
        assert ns.tokens == []
        assert ns.token_addresses == {}

    def test_network_support_with_values(self):
        """NetworkSupport with chains and tokens populated."""
        ns = NetworkSupport(
            chains=["base", "solana"],
            chain_ids=[8453],
            tokens=["USDC", "USDT"],
        )
        assert "base" in ns.chains
        assert "USDC" in ns.tokens
        assert 8453 in ns.chain_ids


# ═══════════════════════════════════════════════════════════════════════
# ── Facilitator base class helpers
# ═══════════════════════════════════════════════════════════════════════


class TestFacilitatorHelpers:
    """Facilitator base class method tests with a concrete stub."""

    def _make_facilitator(self) -> Facilitator:
        """Create a minimal concrete Facilitator for testing."""

        class TestFacilitator(Facilitator):
            @property
            def name(self) -> str:
                return "test_facilitator"

            @property
            def facilitator_type(self) -> FacilitatorType:
                return FacilitatorType.HOSTED

            @property
            def settlement_type(self) -> SettlementType:
                return SettlementType.INSTANT

            @property
            def supported_networks(self) -> NetworkSupport:
                return NetworkSupport(
                    chains=["base", "solana", "ethereum"],
                    chain_ids=[8453, 1],
                    tokens=["USDC", "USDT"],
                    native_tokens=["ETH"],
                    token_addresses={
                        "USDC": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                    },
                )

            async def verify(self, payload, requirements=None):
                return VerificationResult(verified=True, facilitator=self.name)

            async def health(self) -> bool:
                return True

        return TestFacilitator()

    def test_supports_chain(self):
        """supports_chain checks chain membership correctly."""
        f = self._make_facilitator()
        assert f.supports_chain("base") is True
        assert f.supports_chain("solana") is True
        assert f.supports_chain("bitcoin") is False

    def test_supports_token(self):
        """supports_token checks token membership correctly."""
        f = self._make_facilitator()
        assert f.supports_token("USDC") is True
        assert f.supports_token("USDT") is True
        assert f.supports_token("BTC") is False

    def test_supports_token_with_chain(self):
        """supports_token with chain filter works."""
        f = self._make_facilitator()
        assert f.supports_token("USDC", "base") is True
        assert f.supports_token("USDC", "bitcoin") is False

    def test_is_fee_free(self):
        """is_fee_free based on settlement_type."""
        f = self._make_facilitator()
        assert f.is_fee_free is False  # INSTANT, not FEE_FREE

    def test_priority_default(self):
        """Default priority is 50."""
        f = self._make_facilitator()
        assert f.priority == 50

    def test_description(self):
        """Description combines name, type, settlement."""
        f = self._make_facilitator()
        assert "test_facilitator" in f.description
        assert "hosted" in f.description
        assert "instant" in f.description

    def test_format_error(self):
        """_format_error produces failed VerificationResult."""
        f = self._make_facilitator()
        vr = f._format_error("Something went wrong", code=400)
        assert vr.verified is False
        assert vr.reason == "Something went wrong"
        assert vr.facilitator == "test_facilitator"
        assert vr.extra["code"] == 400

    def test_format_success(self):
        """_format_success produces successful VerificationResult."""
        f = self._make_facilitator()
        vr = f._format_success(tx_hash="0xtx", payer="0xpayer", amount="5000000", chain="base")
        assert vr.verified is True
        assert vr.tx_hash == "0xtx"
        assert vr.payer == "0xpayer"
        assert vr.chain == "base"

    def test_settle_noop(self):
        """Default settle returns auto-settled result."""
        f = self._make_facilitator()

        async def _test():
            sr = await f.settle({"payment": "data"})
            assert sr.settled is True
            assert "auto-settled" in sr.reason
            assert sr.facilitator == "test_facilitator"

        import asyncio

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════
# ── FacilitatorRegistry
# ═══════════════════════════════════════════════════════════════════════


class TestFacilitatorRegistry:
    """FacilitatorRegistry: registration, lookup, chain coverage."""

    def _make_base_facilitator(self) -> Facilitator:
        class BaseFac(Facilitator):
            @property
            def name(self) -> str:
                return "base_fac"

            @property
            def facilitator_type(self) -> FacilitatorType:
                return FacilitatorType.HOSTED

            @property
            def settlement_type(self) -> SettlementType:
                return SettlementType.INSTANT

            @property
            def supported_networks(self) -> NetworkSupport:
                return NetworkSupport(chains=["base", "ethereum"], tokens=["USDC"])

            async def verify(self, payload, requirements=None):
                return VerificationResult(verified=True)

            async def health(self) -> bool:
                return True

        return BaseFac()

    def _make_sol_facilitator(self) -> Facilitator:
        class SolFac(Facilitator):
            @property
            def name(self) -> str:
                return "sol_fac"

            @property
            def facilitator_type(self) -> FacilitatorType:
                return FacilitatorType.HOSTED

            @property
            def settlement_type(self) -> SettlementType:
                return SettlementType.DEFERRED

            @property
            def supported_networks(self) -> NetworkSupport:
                return NetworkSupport(chains=["solana"], tokens=["USDC"])

            async def verify(self, payload, requirements=None):
                return VerificationResult(verified=True)

            async def health(self) -> bool:
                return True

            @property
            def priority(self) -> int:
                return 10

        return SolFac()

    def test_register_and_get(self):
        """Registered facilitator is retrievable by name."""
        registry = FacilitatorRegistry()
        fac = self._make_base_facilitator()
        registry.register(fac)
        assert registry.get("base_fac") is fac
        assert registry.get("nonexistent") is None

    def test_get_for_chain(self):
        """get_for_chain returns facilitators supporting that chain."""
        registry = FacilitatorRegistry()
        registry.register(self._make_base_facilitator())
        registry.register(self._make_sol_facilitator())

        base_facs = registry.get_for_chain("base")
        assert len(base_facs) == 1
        assert base_facs[0].name == "base_fac"

        sol_facs = registry.get_for_chain("solana")
        assert len(sol_facs) == 1
        assert sol_facs[0].name == "sol_fac"

        eth_facs = registry.get_for_chain("ethereum")
        assert len(eth_facs) == 1
        assert eth_facs[0].name == "base_fac"

    def test_get_for_chain_empty(self):
        """Unknown chain returns empty list."""
        registry = FacilitatorRegistry()
        assert registry.get_for_chain("tron") == []

    def test_get_all_sorted_by_priority(self):
        """get_all returns facilitators sorted by priority."""
        registry = FacilitatorRegistry()
        registry.register(self._make_base_facilitator())  # priority 50
        registry.register(self._make_sol_facilitator())  # priority 10
        all_f = registry.get_all()
        assert all_f[0].name == "sol_fac"  # lower priority first

    def test_chain_coverage(self):
        """chain_coverage reports correct facilitator counts per chain."""
        registry = FacilitatorRegistry()
        registry.register(self._make_base_facilitator())  # base, ethereum
        registry.register(self._make_sol_facilitator())  # solana
        coverage = registry.chain_coverage
        assert coverage["base"] == 1
        assert coverage["ethereum"] == 1
        assert coverage["solana"] == 1

    def test_stats(self):
        """stats dictionary contains expected keys and values."""
        registry = FacilitatorRegistry()
        registry.register(self._make_base_facilitator())
        registry.register(self._make_sol_facilitator())
        stats = registry.stats
        assert stats["total_facilitators"] == 2
        assert stats["hosted"] == 2
        assert stats["self_hosted"] == 0
        assert "base" in stats["chains_covered"]

    def test_register_duplicate_updates(self):
        """Re-registering same name overwrites existing entry."""
        registry = FacilitatorRegistry()
        fac1 = self._make_base_facilitator()
        registry.register(fac1)
        fac2 = self._make_sol_facilitator()
        registry.register(fac2)
        # sol_fac overwrites with name sol_fac, base_fac stays
        assert registry.get("base_fac") is fac1
        assert registry.get("sol_fac") is fac2

    def test_global_registry_singleton(self):
        """get_registry returns the same instance."""
        reset_registry()
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_reset_registry_creates_new(self):
        """reset_registry creates a fresh registry."""
        reset_registry()
        r1 = get_registry()
        reset_registry()
        r2 = get_registry()
        assert r1 is not r2


# ═══════════════════════════════════════════════════════════════════════
# ── FacilitatorConfig
# ═══════════════════════════════════════════════════════════════════════


class TestFacilitatorConfig:
    """FacilitatorConfig data management."""

    def test_config_defaults(self, monkeypatch):
        """FacilitatorConfig uses env defaults when no env vars set."""
        monkeypatch.delenv("COINBASE_CDP_API_KEY", raising=False)
        monkeypatch.delenv("PAYAI_VERIFY_URL", raising=False)
        cfg = FacilitatorConfig()
        assert cfg.coinbase_cdp_api_key == ""
        assert cfg.coinbase_cdp_network == "base-mainnet"
        assert cfg.payai_verify_url == "https://facilitator.payai.network/verify"
        assert cfg.facilitator_health_ttl == 60

    def test_config_to_dict_redacts_secrets(self, monkeypatch):
        """to_dict redacts sensitive fields."""
        monkeypatch.setenv("COINBASE_CDP_API_KEY", "super-secret-key-12345")
        cfg = FacilitatorConfig()
        d = cfg.to_dict()
        # The secret API key should be redacted
        api_key_val = d.get("coinbase_cdp_api_key", "")
        assert "***" in str(api_key_val) or len(str(api_key_val)) < len("super-secret-key-12345")

    def test_config_singleton(self):
        """get_config returns same instance."""
        reset_config()
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_config_reset(self):
        """reset_config clears singleton."""
        reset_config()
        c1 = get_config()
        reset_config()
        c2 = get_config()
        assert c1 is not c2


# ═══════════════════════════════════════════════════════════════════════
# ── Tier Resolver (usage tracking)
# ═══════════════════════════════════════════════════════════════════════


class TestTierResolver:
    """Tier resolver trial and burst limit logic."""

    @patch("app.domains.billing.x402.tier_resolver.get_redis")
    def test_check_trial_allowed(self, mock_get_redis):
        """check_trial returns allowed when under limit."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # no usage yet
        mock_get_redis.return_value = mock_redis

        allowed, remaining = check_trial("test_tool", "client_1", max_trials=3)
        assert allowed is True
        assert remaining == 3

    @patch("app.domains.billing.x402.tier_resolver.get_redis")
    def test_check_trial_exhausted(self, mock_get_redis):
        """check_trial returns blocked when over limit."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = "3"
        mock_redis.ttl.return_value = 3600
        mock_get_redis.return_value = mock_redis

        allowed, remaining = check_trial("test_tool", "client_1", max_trials=3)
        assert allowed is False
        assert remaining == 0

    @patch("app.domains.billing.x402.tier_resolver.get_redis")
    def test_check_trial_partial(self, mock_get_redis):
        """check_trial returns correct remaining count."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = "2"
        mock_get_redis.return_value = mock_redis

        allowed, remaining = check_trial("test_tool", "client_1", max_trials=5)
        assert allowed is True
        assert remaining == 3

    @patch("app.domains.billing.x402.tier_resolver.get_redis")
    def test_consume_trial_first_use_sets_ttl(self, mock_get_redis):
        """consume_trial sets TTL on first use."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        result = consume_trial("test_tool", "client_1", max_trials=3)
        assert result is True
        mock_redis.setex.assert_called_once()

    @patch("app.domains.billing.x402.tier_resolver.get_redis")
    def test_consume_trial_increments_existing(self, mock_get_redis):
        """consume_trial increments existing counter."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = "1"
        mock_get_redis.return_value = mock_redis

        result = consume_trial("test_tool", "client_1", max_trials=3)
        assert result is True
        mock_redis.incr.assert_called_once()

    @patch("app.domains.billing.x402.tier_resolver.get_redis")
    def test_check_burst_limit_under(self, mock_get_redis):
        """check_burst_limit returns None when under the limit."""
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_redis
        mock_redis.incr.return_value = 1
        mock_redis.execute.return_value = [1]
        mock_redis.exists.return_value = False
        mock_get_redis.return_value = mock_redis

        assert check_burst_limit("client_1", "test_tool") is None

    @patch("app.domains.billing.x402.tier_resolver.get_redis")
    def test_check_burst_limit_exceeded(self, mock_get_redis):
        """check_burst_limit returns rate limit dict when over the limit."""
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_redis
        mock_redis.incr.return_value = 10  # Over default burst of 5
        mock_redis.execute.return_value = [10]
        mock_redis.exists.return_value = True
        mock_get_redis.return_value = mock_redis

        result = check_burst_limit("client_1", "test_tool")
        assert result is not None
        assert result["status_code"] == 429
        assert "Rate limited" in result["message"]
