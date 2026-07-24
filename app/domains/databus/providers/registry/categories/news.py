"""
News provider chains.
"""

from ..base import Provider, ProviderChain, ProviderTier


def build_news_chains(locals_dict: dict) -> dict[str, ProviderChain]:
    """Build news provider chains."""
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict

    chains["full_news"] = ProviderChain(
            "full_news",
            description="Complete news feed: headlines + market data + fear/greed + polymarket predictions",
            providers=[
                Provider(
                    "full_news_feed",
                    ProviderTier.LOCAL,
                    get_full_news_feed,
                    weight=15.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

    return chains
