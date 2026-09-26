"""
Security provider chains — token security & volume authenticity.

Chains: volume_authenticity, token_security, token_launches,
token_security_free. ``volume_authenticity`` and ``token_security`` are
duplicated in ``_generated/chains/premium.py``; kept here for backward compat
until the registry is fully deduplicated. ``token_security_free`` is the free
multi-tier fallback (Arkham-quality replacement). ``token_launches`` is
registered HERE (not in ``chain_indexer``) to keep the historical
registration order of ``build_security_chains``. Split from ``security.py``
(see ``security/__init__.py``).
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

from ....base import Provider, ProviderChain, ProviderTier

logger = get_logger("security")


def build_token_security_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build the token-security chains (two optional, two local)."""
    chains: dict[str, ProviderChain] = {}
    chains.update(_volume_authenticity())
    chains.update(_token_security())
    chains.update(_token_launches(g))
    chains.update(_token_security_free(g))
    return chains


def _volume_authenticity() -> dict[str, ProviderChain]:
    """Fake-volume detection via ``app.databus.volume_authenticity`` (optional)."""
    try:
        from app.databus.volume_authenticity import analyze_volume_authenticity
    except ImportError as e:
        logger.debug(f"build_security_chains failed: {e}")
        return {}
    return {
        "volume_authenticity": ProviderChain(
            "volume_authenticity",
            description="Fake volume detection",
            providers=[
                Provider(
                    "volume_auth_scorer",
                    ProviderTier.LOCAL,
                    analyze_volume_authenticity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
    }


def _token_security() -> dict[str, ProviderChain]:
    """37+ security checks via ``app.databus.token_security`` (optional)."""
    try:
        from app.databus import token_security as token_security_mod
    except ImportError as e:
        logger.debug(f"build_security_chains failed: {e}")
        return {}
    # Bound as module attributes so the type checker sees the declared
    # signatures rather than a partially-unknown import alias.
    run_full_scan = token_security_mod.run_full_scan
    get_check_matrix_endpoint = token_security_mod.get_check_matrix_endpoint
    return {
        "token_security": ProviderChain(
            "token_security",
            description="37+ security checks",
            providers=[
                Provider(
                    "security_scanner",
                    ProviderTier.LOCAL,
                    run_full_scan,
                    weight=10.0,
                    rate_limit_rps=10.0,
                ),
                Provider(
                    "check_matrix",
                    ProviderTier.LOCAL,
                    get_check_matrix_endpoint,
                    weight=1.0,
                    rate_limit_rps=60.0,
                ),
            ],
        )
    }


def _token_launches(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """New token launch detection — always available via chain-indexer ClickHouse."""
    return {
        "token_launches": ProviderChain(
            "token_launches",
            description="New token launch detection via chain-indexer ClickHouse deployer_events",
            providers=[
                Provider(
                    "lite_chain_indexer_deployer",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_deployer_history"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse deployer_events — new token launches from indexed chains",
                ),
            ],
        )
    }


def _local_free_provider(g: dict[str, Any]) -> Provider:
    """Own ClickHouse security analysis — first tier, zero cost."""
    return Provider(
        "free_security_clickhouse",
        ProviderTier.LOCAL,
        g["_free_security_clickhouse"],
        weight=20.0,
        is_local=True,
        description="Own ClickHouse security analysis (evm_transfers + deployer_events) — zero cost",
    )


def _evm_free_providers(g: dict[str, Any]) -> list[Provider]:
    """GoPlus + Honeypot.is — EVM-focused free security checks."""
    return [
        Provider(
            "free_security_goplus",
            ProviderTier.FREE_API,
            g["_free_security_goplus"],
            weight=10.0,
            rate_limit_rps=10.0,
            description="GoPlus Security API — honeypot, mint, tax, owner checks (21+ chains, free)",
        ),
        Provider(
            "free_security_honeypot_is",
            ProviderTier.FREE_API,
            g["_free_security_honeypot_is"],
            weight=8.0,
            rate_limit_rps=2.0,
            description="Honeypot.is — simulated buy/sell, honeypot flag (EVM chains, free)",
        ),
    ]


def _market_free_providers(g: dict[str, Any]) -> list[Provider]:
    """RugCheck (Solana) + DexScreener (market/liquidity) — last tiers."""
    return [
        Provider(
            "free_security_rugcheck",
            ProviderTier.FREE_API,
            g["_free_security_rugcheck"],
            weight=6.0,
            rate_limit_rps=2.0,
            description="RugCheck.xyz — Solana token risk report (free)",
        ),
        Provider(
            "free_security_dexscreener",
            ProviderTier.FREE_API,
            g["_free_security_dexscreener"],
            weight=4.0,
            rate_limit_rps=2.0,
            description="DexScreener pair heuristics — buy/sell asymmetry, liquidity depth (free)",
        ),
    ]


def _token_security_free(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Free stacked fallback replacing the paid/Goplus-only checks.

    Tier order (highest weight first; the circuit breaker skips open ones):
    own ClickHouse → GoPlus → Honeypot.is → RugCheck → DexScreener. Each
    returns None on failure, so ``ProviderChain.fetch()`` falls through.
    """
    providers = [_local_free_provider(g), *_evm_free_providers(g), *_market_free_providers(g)]
    return {
        "token_security_free": ProviderChain(
            "token_security_free",
            description="Free token security fallback: ClickHouse → GoPlus → Honeypot.is → RugCheck → DexScreener heuristics",
            providers=providers,
        ),
    }
