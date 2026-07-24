"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

Chain aggregation module. Imports builder functions from each sub-module
and composes them into the master build_provider_chains() function.
"""

from app.core.logging import get_logger
from app.databus.provider_core import (
    ProviderChain,
    _circuit_breakers,
    _CircuitBreaker,
    _rate_limiters,
    _RateLimiter,
)
from app.domains.databus._generated.chains.alchemy_arkham import build_alchemy_arkham_chains
from app.domains.databus._generated.chains.bitquery import build_bitquery_chains
from app.domains.databus._generated.chains.core import build_core_chains
from app.domains.databus._generated.chains.misc import build_misc_chains
from app.domains.databus._generated.chains.news_social import build_news_social_chains
from app.domains.databus._generated.chains.onchain import build_onchain_chains
from app.domains.databus._generated.chains.premium import build_premium_chains
from app.domains.databus._generated.chains.rugcharts import build_rugcharts_chains

logger = get_logger("databus.providers.chains")


def build_provider_chains() -> dict[str, ProviderChain]:
    """Build all fallback chains for every data type.

    Delegates to focused builder functions in sub-modules, then
    initializes circuit breakers and rate limiters for all providers.
    """
    chains: dict[str, ProviderChain] = {}

    # Build from each sub-module
    build_core_chains(chains)
    build_bitquery_chains(chains)
    build_onchain_chains(chains)
    build_alchemy_arkham_chains(chains)
    build_premium_chains(chains)
    build_rugcharts_chains(chains)
    build_news_social_chains(chains)
    build_misc_chains(chains)

    # Initialize circuit breakers and rate limiters
    for chain in chains.values():
        for provider in chain.providers:
            if provider.name not in _circuit_breakers:
                _circuit_breakers[provider.name] = _CircuitBreaker(
                    threshold=provider.failure_threshold, timeout=provider.recovery_timeout
                )
            if provider.name not in _rate_limiters:
                _rate_limiters[provider.name] = _RateLimiter(rps=provider.rate_limit_rps)

    logger.info(
        f"Built {len(chains)} provider chains with {sum(len(c.providers) for c in chains.values())} total providers"
    )
    return chains
