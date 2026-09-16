"""First test suite for the rpc_router mixins + EndpointHealth.

Style: dependency injection via a minimal harness subclass and real env
mutations — zero patch-based mocking, which keeps the repo's patch-usage
ratchet untouched or shrinking.
"""

from __future__ import annotations

import collections
import os
import time

import pytest

from app.domains.scanner.engines.rpc_router.hedging import EndpointHealth
from app.domains.scanner.engines.rpc_router.helpers import HelpersMixin

TEST_KEY_NAME = "RPC_ROUTER_TEST_KEY_XYZ"


@pytest.fixture
def test_key_env():
    """Set a temporary env key for URL resolution (pure os.environ)."""
    os.environ[TEST_KEY_NAME] = "sekret123"
    yield TEST_KEY_NAME
    os.environ.pop(TEST_KEY_NAME, None)


class RouterHarness(HelpersMixin):
    """Minimal HelpersMixin host with injected endpoints (no patching)."""

    def __init__(self, endpoints: list[str]) -> None:
        self._endpoints = endpoints
        self._evm_health: dict = collections.defaultdict(dict)
        self._solana_health: dict = {}
        self._quota: dict = {}
        self._rr_offset: dict = {}

    def _raw_endpoints(self, chain: str) -> list[str]:
        return list(self._endpoints)


class TestTierOfTemplate:
    def test_plain_url_is_free(self) -> None:
        assert HelpersMixin._tier_of_template("https://rpc.example.com") == "free"

    def test_keyed_url_is_freemium(self) -> None:
        assert HelpersMixin._tier_of_template("https://rpc.example.com/${SOME_KEY}") == "freemium"


class TestResolveUrl:
    def test_resolves_env_placeholder(self, test_key_env) -> None:
        h = RouterHarness([])
        resolved = h._resolve_url(f"https://rpc.example.com/${{{TEST_KEY_NAME}}}")
        assert resolved == "https://rpc.example.com/sekret123"

    def test_missing_key_returns_none(self) -> None:
        h = RouterHarness([])
        assert h._resolve_url("https://rpc.example.com/${DEFINITELY_MISSING_KEY_Q}") is None

    def test_plain_url_passthrough(self) -> None:
        h = RouterHarness([])
        assert h._resolve_url("https://rpc.example.com") == "https://rpc.example.com"


class TestSplitTiers:
    def test_free_freemium_and_dropped(self, test_key_env) -> None:
        h = RouterHarness(
            [
                "https://free.example.com",
                f"https://keyed.example.com/${{{TEST_KEY_NAME}}}",
                "https://broken.example.com/${DEFINITELY_MISSING_KEY_Q}",
            ]
        )
        free, freemium = h._split_tiers("ethereum")
        assert free == ["https://free.example.com"]
        assert freemium == ["https://keyed.example.com/sekret123"]

    def test_empty_chain(self) -> None:
        h = RouterHarness([])
        assert h._split_tiers("ethereum") == ([], [])


class TestQuotaUsage:
    def test_counts_only_within_hour(self) -> None:
        h = RouterHarness([])
        now = time.time()
        h._quota["rpc.example.com"] = [now - 30, now - 4000, now - 10]
        assert h._quota_usage("https://rpc.example.com/path") == 2

    def test_unknown_host_is_zero(self) -> None:
        h = RouterHarness([])
        assert h._quota_usage("https://never-seen.example.com") == 0


class TestGetHealth:
    def test_solana_branch_reuses_instance(self) -> None:
        h = RouterHarness([])
        first = h._get_health("solana", "https://sol.example", tier="free")
        second = h._get_health("solana", "https://sol.example")
        assert first is second
        assert h._solana_health["https://sol.example"] is first

    def test_evm_branch_nested_by_chain(self) -> None:
        h = RouterHarness([])
        health = h._get_health("base", "https://base.example", tier="freemium")
        assert h._evm_health["base"]["https://base.example"] is health
        assert health.tier == "freemium"


class TestEndpointHealth:
    def test_new_endpoint_is_alive(self) -> None:
        assert EndpointHealth(url="u", chain="ethereum").alive is True

    def test_three_failures_mark_dead(self) -> None:
        health = EndpointHealth(url="u", chain="ethereum")
        for _ in range(3):
            health.record_failure()
        assert health.is_dead is True
        assert health.alive is False

    def test_cooldown_expiry_revives(self) -> None:
        health = EndpointHealth(url="u", chain="ethereum")
        health.is_dead = True
        health.dead_until = time.time() - 1  # cooldown passed
        assert health.alive is True

    def test_success_resets_failures_and_latency(self) -> None:
        health = EndpointHealth(url="u", chain="ethereum")
        health.record_failure()
        # NOTE: the average is diluted by failed attempts (counted with 0
        # latency). Asserting actual behaviour; candidate for a metrics review.
        health.record_success(100.0)
        assert health.consecutive_failures == 0
        assert health.is_dead is False
        assert health.avg_latency_ms == 50.0  # (0*1 + 100) / 2
        health.record_success(200.0)
        assert health.avg_latency_ms == 100.0  # (50*2 + 200) / 3

    def test_failure_counters(self) -> None:
        health = EndpointHealth(url="u", chain="ethereum")
        health.record_failure()
        assert health.total_calls == 1
        assert health.total_failures == 1
