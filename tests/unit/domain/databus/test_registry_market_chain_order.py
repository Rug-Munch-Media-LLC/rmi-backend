"""Chain-order golden test for the assembled market provider chains.

Covers what the market FACADE produces: the registration order of all 28
market chains. The split into ``market/*.py`` regrouped chains by theme and
reordered them; this test is what keeps that from happening again. Provider
attributes are not pinned here — the order is the thing a split silently
breaks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.domains.databus.providers.registry.categories.market import build_market_chains
from tests.unit.domain.databus.market_chain_golden import (
    GOLDEN_MARKET_CHAIN_ORDER,
    _locals_dict,
)

if TYPE_CHECKING:
    from app.domains.databus.providers.base import ProviderChain


@pytest.fixture(scope="module")
def chains() -> dict[str, ProviderChain]:
    return build_market_chains(_locals_dict())


def test_chain_order_is_unchanged(chains: dict[str, ProviderChain]) -> None:
    assert tuple(chains) == GOLDEN_MARKET_CHAIN_ORDER


def test_every_chain_has_a_description(chains: dict[str, ProviderChain]) -> None:
    for name, chain in chains.items():
        assert chain.data_type == name, f"{name} has data_type {chain.data_type!r}"
        assert chain.description, f"{name} lost its description"
