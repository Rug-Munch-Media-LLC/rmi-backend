"""Golden tests for per-module security chain ownership and collisions.

The split moved 631 lines of ``security.py`` into five focused sub-modules.
These tests pin WHAT EACH MODULE OWNS: that every sub-builder returns only
chains from the frozen facade set, that the optional-import module (rugcharts)
takes no registry frame, and that the single known chain-name collision is
still exactly the pre-existing ``volume_authenticity`` pair.

Patch-free: see ``security_chain_golden`` for why.
"""

from __future__ import annotations

import pytest

from app.domains.databus.providers.base import ProviderChain
from app.domains.databus.providers.registry.categories.security.chain_indexer import (
    build_chain_indexer_chains,
)
from app.domains.databus.providers.registry.categories.security.composite import (
    build_composite_chains,
)
from app.domains.databus.providers.registry.categories.security.core import build_core_chains
from app.domains.databus.providers.registry.categories.security.rugcharts import (
    build_rugcharts_chains,
)
from app.domains.databus.providers.registry.categories.security.token_security import (
    build_token_security_chains,
)
from tests.unit.domain.databus.security_chain_golden import GOLDEN_CHAIN_ORDER, _locals_dict

SUB_BUILDERS: dict[str, object] = {
    "core": build_core_chains,
    "token_security": build_token_security_chains,
    "rugcharts": build_rugcharts_chains,
    "chain_indexer": build_chain_indexer_chains,
    "composite": build_composite_chains,
}

# Only rugcharts has no frame parameter — all nine of its chains come from one
# optional import, so there is nothing to inject.
FRAME_READERS = ("core", "token_security", "chain_indexer", "composite")


def _run(module: str) -> dict[str, ProviderChain]:
    if module in FRAME_READERS:
        return SUB_BUILDERS[module](_locals_dict())  # ty: ignore[invalid-argument-type]
    return SUB_BUILDERS[module]()  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("builder", "reads_frame"),
    [
        (build_core_chains, True),
        (build_token_security_chains, True),
        (build_rugcharts_chains, False),
        (build_chain_indexer_chains, True),
        (build_composite_chains, True),
    ],
)
def test_sub_builders_return_their_own_chains(builder, reads_frame: bool) -> None:
    """Each module owns its chains; optional-only modules may be empty.

    ``build_rugcharts_chains`` takes no frame because all nine of its chains
    come from one optional import, so there is nothing to inject.
    """
    out = builder(_locals_dict()) if reads_frame else builder()
    assert set(out) <= set(GOLDEN_CHAIN_ORDER)
    assert all(isinstance(c, ProviderChain) for c in out.values())


def test_only_known_volume_authenticity_collides() -> None:
    """Two modules build volume_authenticity — and nothing else does.

    Guards against a NEW silent overwrite being introduced by a future split.
    """
    collisions: dict[str, list[str]] = {}
    for module in SUB_BUILDERS:
        for name in _run(module):
            collisions.setdefault(name, []).append(module)
    duplicated = {n: mods for n, mods in collisions.items() if len(mods) > 1}
    assert duplicated == {"volume_authenticity": ["token_security", "chain_indexer"]}
    assert set(collisions) == set(GOLDEN_CHAIN_ORDER)
