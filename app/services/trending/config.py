"""Weights, quote-token sets and chain support for trending scoring.

Every number here is a *transparent* knob: the API returns the applied weight
next to each component, so a client can re-derive the score.
"""

from __future__ import annotations

# Component weights. Missing factors are dropped and remaining weights are
# renormalised (never filled with an invented value).
WEIGHTS: dict[str, float] = {
    "volume_liquidity": 0.20,
    "tx_count": 0.20,
    "holder_growth": 0.15,
    "price_momentum": 0.20,
    "age": 0.10,
    "social_sentiment": 0.10,
    "source_agreement": 0.05,
}

# Normalisation scales (log10 of the raw metric at which the factor saturates).
TX_SATURATION = 1e5  # 100k transfers in the window == 1.0
VOLUME_SATURATION_USD = 1e7  # $10M volume in the window == 1.0
AGE_HALF_LIFE_HOURS = 72.0  # a 3-day-old launch scores 0.5 on freshness

# Anti-manipulation thresholds -> (threshold, max_penalty)
WASH_SWAPS_PER_WALLET = 10.0
WASH_MAX_PENALTY = 0.50
CONCENTRATION_SHARE = 0.50
CONCENTRATION_MAX_PENALTY = 0.50
BUNDLE_BLOCKS = 3
BUNDLE_MAX_PENALTY = 0.30
BOOST_MAX_PENALTY = 0.40
BOOST_LOW_VOLUME_USD = 5_000.0

# Quote assets: pair volume is attributed to the *other* token.
QUOTE_TOKENS: dict[str, tuple[str, ...]] = {
    "ethereum": (
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0x6b175474e89094c44da98b954eedeac495271d0f",
    ),
    "bsc": (
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        "0x55d398326f99059ff775485246999027b3197955",
        "0xe9e7cea3dedca5984780bafc599bd69add087d56",
    ),
    "polygon": (
        "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
    ),
    "arbitrum": ("0x82af49447d8a07e3bd95bd0d56f35241523fbab1",),
    "optimism": ("0x4200000000000000000000000000000000000006",),
    "base": ("0x4200000000000000000000000000000000000006",),
}

# Solana quote mints (wrapped SOL, USDC, USDT).
SOLANA_QUOTES: tuple[str, ...] = (
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
)

# chains with live indexer tables. Everything else -> null score + reason.
EVM_CHAINS = frozenset(
    {
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "base",
        "avalanche",
        "fantom",
        "gnosis",
        "linea",
        "scroll",
        "zksync",
        "mantle",
        "celo",
        "tron",
    }
)
SOLANA_CHAINS = frozenset({"solana"})
SUPPORTED_CHAINS = tuple(sorted(EVM_CHAINS | SOLANA_CHAINS))

SCORE_FORMULA = (
    "score = 100 * clamp(sum(w_i * f_i) / sum(w_i), 0, 1) * penalty_multiplier; "
    "f_i in [0,1] per component; missing factors are omitted (never imputed)."
)
