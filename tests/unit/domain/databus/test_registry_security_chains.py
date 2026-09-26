"""Golden tests for the assembled security provider-chain order and attributes.

Covers what the FACADE produces: registration order (including the pinned
``volume_authenticity`` duplicate), per-provider attributes, tier sanity and
the marker-callable wiring. Per-module ownership and collision detection live
in ``test_registry_security_modules.py``.

Patch-free: see ``security_chain_golden`` for why. Expected values are frozen
from the pre-split 631-line ``security.py``; the per-provider attribute golden
lives in ``security_provider_golden``.
"""

from __future__ import annotations

import inspect

import pytest

from app.domains.databus.providers.base import ProviderChain, ProviderTier
from app.domains.databus.providers.registry.categories.security import build_security_chains
from tests.unit.domain.databus.security_chain_golden import (
    GOLDEN_ASSIGNED_ORDER,
    GOLDEN_CHAIN_ORDER,
    LOCAL_FRAME_CHAINS,
    MARKER,
    _locals_dict,
)
from tests.unit.domain.databus.security_provider_golden import GOLDEN_PROVIDERS

# Providers whose fetch fn must resolve to a registry-frame marker callable.
MARKED_PROVIDER_NAMES = {
    "virustotal_scan",
    "sentinel_scanner",
    "local_rag_scam_db",
    "local_rag",
    "spl_metadata_decoder",
    "scraped_intel_local",
    "dexscreener_holders",
    "birdeye_overview_api",
}

MARKED_PROVIDER_PREFIXES = ("free_security_", "lite_chain_indexer_")


@pytest.fixture(scope="module")
def chains() -> dict[str, ProviderChain]:
    return build_security_chains(_locals_dict())


def test_chain_order_is_unchanged(chains: dict[str, ProviderChain]) -> None:
    assert tuple(chains) == GOLDEN_CHAIN_ORDER


def test_volume_authenticity_duplicate_shadowing_is_preserved(chains: dict[str, ProviderChain]) -> None:
    """The optional volume_authenticity chain is registered, then overwritten.

    ``_volume_authenticity`` (optional app.databus import) runs before
    ``_flow_chains`` (LOCAL ClickHouse), so the ClickHouse provider wins and
    the dict keeps only the FIRST insertion position with the LAST value.
    Both halves are pinned so a future dedupe is a deliberate, visible change.
    """
    assert "volume_authenticity" in chains
    assert [p.name for p in chains["volume_authenticity"].providers] == ["lite_chain_indexer_volume_authenticity"]
    assert GOLDEN_ASSIGNED_ORDER.count("volume_authenticity") == 2
    assert GOLDEN_CHAIN_ORDER.count("volume_authenticity") == 1


def test_data_type_matches_chain_name(chains: dict[str, ProviderChain]) -> None:
    for name, chain in chains.items():
        assert chain.data_type == name, f"{name} has data_type {chain.data_type!r}"
        assert chain.description, f"{name} lost its description"


def test_provider_attributes_are_unchanged(chains: dict[str, ProviderChain]) -> None:
    for name, expected in GOLDEN_PROVIDERS.items():
        actual = tuple(
            (p.name, p.tier.value, p.weight, p.is_local, p.requires_key, p.key_env, p.monthly_quota)
            for p in chains[name].providers
        )
        assert actual == expected, f"{name} provider attributes drifted"


def test_all_tiers_are_known(chains: dict[str, ProviderChain]) -> None:
    for name, chain in chains.items():
        for provider in chain.providers:
            assert isinstance(provider.tier, ProviderTier), name
            assert provider.weight > 0, name


def _is_frame_provider(provider_name: str) -> bool:
    return provider_name in MARKED_PROVIDER_NAMES or provider_name.startswith(MARKED_PROVIDER_PREFIXES)


def test_frame_providers_resolve_to_their_own_marker(chains: dict[str, ProviderChain]) -> None:
    for name in sorted(LOCAL_FRAME_CHAINS):
        for provider in chains[name].providers:
            if _is_frame_provider(provider.name):
                assert provider.fetch_fn().startswith(MARKER), f"{name}.{provider.name}"


def test_facade_signature_is_stable() -> None:
    """The registry calls build_security_chains(locals()) positionally."""
    params = list(inspect.signature(build_security_chains).parameters)
    assert params == ["locals_dict"]
