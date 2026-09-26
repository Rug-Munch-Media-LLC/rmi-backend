"""
Security provider chains — rugcharts identity/reputation cluster.

Chains: cross_chain_entity, rug_patterns, dev_reputation, token_report.

All four read from the same optional module (``app.databus.rugcharts_intel``)
as the sibling ``rugcharts`` cluster, so they share its availability probe: a
missing module skips them, exactly as the pre-split builder did. Split out of
``rugcharts.py`` to keep both clusters under the 200-line hard limit.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_rugcharts_identity_chains(
    entity: Any, rug_patterns: Any, dev_reputation: Any, token_report: Any
) -> dict[str, ProviderChain]:
    """Entity resolution, rug matcher, dev reputation, enhanced report.

    Takes the already-imported callables (not the module) so the sibling
    ``rugcharts`` cluster owns the single availability probe for both.
    """
    chains: dict[str, ProviderChain] = {}
    chains.update(_entity_resolution_chains(entity))
    chains.update(_reputation_chains(rug_patterns, dev_reputation))
    chains.update(_token_report_chain(token_report))
    return chains


def _entity_resolution_chains(entity: Any) -> dict[str, ProviderChain]:
    """Cross-chain entity resolution."""
    return {
        "cross_chain_entity": ProviderChain(
            "cross_chain_entity",
            description="Cross-chain entity resolution",
            providers=[
                Provider(
                    "entity_tracer",
                    ProviderTier.LOCAL,
                    entity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        ),
    }


def _reputation_chains(rug_patterns: Any, dev_reputation: Any) -> dict[str, ProviderChain]:
    """Rug pull pattern matcher + developer reputation."""
    return {
        "rug_patterns": ProviderChain(
            "rug_patterns",
            description="Rug pull pattern matcher",
            providers=[
                Provider(
                    "rug_matcher",
                    ProviderTier.LOCAL,
                    rug_patterns,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        ),
        "dev_reputation": ProviderChain(
            "dev_reputation",
            description="Developer reputation",
            providers=[
                Provider(
                    "dev_reputation_api",
                    ProviderTier.LOCAL,
                    dev_reputation,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        ),
    }


def _token_report_chain(token_report: Any) -> dict[str, ProviderChain]:
    """Enhanced token report — heaviest single provider in the cluster."""
    return {
        "token_report": ProviderChain(
            "token_report",
            description="Enhanced token report",
            providers=[
                Provider(
                    "report_generator",
                    ProviderTier.LOCAL,
                    token_report,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        ),
    }
