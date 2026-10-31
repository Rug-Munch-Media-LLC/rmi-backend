"""Quote / stable / wrapped-native address sets.

These are real assets, but they are not what a "trending token" feed means.
They are not dropped silently: ``quote_penalty`` shrinks their score so they
cannot crowd out the long tail, while the row stays inspectable.
"""

from __future__ import annotations

WRAPPED_NATIVE: dict[str, frozenset[str]] = {
    "solana": frozenset({"So11111111111111111111111111111111111111112"}),
    "ethereum": frozenset({"0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"}),
    "arbitrum": frozenset({"0x82af49447d8a07e3bd95bd0d56f35241523fbab1"}),
    "optimism": frozenset({"0x4200000000000000000000000000000000000006"}),
    "base": frozenset({"0x4200000000000000000000000000000000000006"}),
    "polygon": frozenset(
        {
            "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",  # WPOL
            "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",  # WETH
        }
    ),
    "avalanche": frozenset({"0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"}),
    "fantom": frozenset({"0x21be370d5312f44cb42ce377bc9b8a0cef1a4c83"}),
    "gnosis": frozenset({"0xe91d153e0b41518a2ce8dd3d7944fa863463a97d"}),
    "bsc": frozenset({"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"}),
}

STABLES: frozenset[str] = frozenset(
    {
        # Solana
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
        "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB",
        # EVM
        "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
        "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # BSC USDC
        "0x55d398326f99059ff775485246999027b3197955",  # BSC USDT
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",  # Polygon USDC.e
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",  # Polygon USDC
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",  # Polygon USDT
        "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",  # Arbitrum USDC.e
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # Arbitrum USDC
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # Arbitrum USDT
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # Base USDC
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # Base USDbC
        "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7",  # Avalanche USDT
        "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e",  # Avalanche USDC
    }
)


def is_stable(token: str) -> bool:
    """True when the token is a USD stablecoin."""
    return token in STABLES


def is_wrapped_native(chain: str, token: str) -> bool:
    """True when the token is the chain's wrapped native asset."""
    return token in WRAPPED_NATIVE.get(chain, frozenset())


def quote_penalty(chain: str, token: str) -> float:
    """Score multiplier that keeps quote assets from dominating the feed."""
    if is_stable(token):
        return 0.02
    if is_wrapped_native(chain, token):
        return 0.05
    return 1.0
