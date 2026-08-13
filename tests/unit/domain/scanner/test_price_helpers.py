"""Unit tests for the degodded price oracle split modules.

Covers ``_price_helpers.py`` (chain/token normalization + weighted price
collection) and ``_price_constants.py`` (TTLs, wrapped-native map,
stablecoin allowlist, decimals fallback). Pure — no I/O.
"""

from __future__ import annotations

from app.domains.scanner.engines._price_constants import (
    _FALLBACK_DECIMALS,
    _MEM_MAX,
    _MEM_TTL,
    _NATIVE_TTL,
    _PRICE_TTL,
    _STABLECOINS,
    _WRAPPED_NATIVE,
)
from app.domains.scanner.engines._price_helpers import _collect_weighted_prices, _normalize_chain, _normalize_token

# ── chain normalization ────────────────────────────────────────────────────


def test_normalize_chain_aliases() -> None:
    assert _normalize_chain("ETH") == "ethereum"
    assert _normalize_chain("eth") == "ethereum"
    assert _normalize_chain("BNB") == "bsc"
    assert _normalize_chain("MATIC") == "polygon"
    assert _normalize_chain("ARB") == "arbitrum"
    assert _normalize_chain("OP") == "optimism"
    assert _normalize_chain("AVAX") == "avalanche"
    assert _normalize_chain("FTM") == "fantom"
    assert _normalize_chain("solana") == "solana"
    assert _normalize_chain("SOLANA") == "solana"


def test_normalize_chain_unknown_lowercases() -> None:
    assert _normalize_chain("Celestia") == "celestia"


# ── token address normalization ────────────────────────────────────────────


def test_normalize_token_evm_lowercases() -> None:
    assert _normalize_token(" 0xABC123 ", "ethereum") == "0xabc123"
    assert _normalize_token("0xDEF", "bsc") == "0xdef"


def test_normalize_token_solana_preserves_case() -> None:
    addr = "So11111111111111111111111111111111111111112"
    assert _normalize_token(addr, "solana") == addr
    assert _normalize_token(" EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v ", "solana") == (
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    )


# ── weighted price collection ──────────────────────────────────────────────


def _swap(token0: str, token1: str, **amounts: float) -> dict:
    s: dict = {"token0": token0, "token1": token1}
    s.update(amounts)
    return s


def test_collect_weighted_prices_buy_and_sell() -> None:
    swaps = [
        # token0=our token: sell → our token in (100), paired out (200) → price 2.0
        _swap("0xaaa", "0xccc", amount0_in=100, amount0_out=0, amount1_in=0, amount1_out=200),
        # token1=our token: buy → our token out (50), paired in (300) → price 6.0
        _swap("0xccc", "0xaaa", amount0_in=300, amount0_out=0, amount1_in=0, amount1_out=50),
    ]
    prices, paired = _collect_weighted_prices(swaps, "0xaaa")
    assert prices == [(2.0, 100.0), (6.0, 50.0)]
    assert paired == {"0xccc": 2}


def test_collect_weighted_prices_strict_match_skips_unrelated() -> None:
    swaps = [_swap("0xbbb", "0xccc", amount0_in=0, amount0_out=100, amount1_in=200, amount1_out=0)]
    assert _collect_weighted_prices(swaps, "0xaaa")[0] == []
    # non-strict trusts the caller's filtered set: token1 side is treated as
    # the paired side, so price = paired_out(100) / our_in(200) = 0.5
    assert _collect_weighted_prices(swaps, "0xaaa", strict_match=False)[0] == [(0.5, 200.0)]


def test_collect_weighted_prices_uses_pair_cache_for_empty_columns() -> None:
    swaps = [_swap("", "", contract_address="0xpool", amount0_in=0, amount0_out=100, amount1_in=200, amount1_out=0)]
    cache = {"0xpool": ("0xAAA", "0xCCC")}
    prices, paired = _collect_weighted_prices(swaps, "0xaaa", pair_cache=cache)
    assert prices == [(2.0, 100.0)]
    assert paired == {"0xccc": 1}


def test_collect_weighted_prices_pair_cache_miss_skips() -> None:
    swaps = [_swap("", "", contract_address="0xpool", amount0_in=0, amount0_out=100, amount1_in=200, amount1_out=0)]
    assert _collect_weighted_prices(swaps, "0xaaa")[0] == []
    assert _collect_weighted_prices(swaps, "0xaaa", pair_cache={})[0] == []


def test_collect_weighted_prices_skips_zero_weight() -> None:
    swaps = [_swap("0xaaa", "0xccc", amount0_in=0, amount0_out=0, amount1_in=0, amount1_out=0)]
    assert _collect_weighted_prices(swaps, "0xaaa")[0] == []


def test_collect_weighted_prices_skips_when_no_price_can_be_derived() -> None:
    swaps = [_swap("0xaaa", "0xccc", amount0_in=0, amount0_out=100, amount1_in=0, amount1_out=0)]
    assert _collect_weighted_prices(swaps, "0xaaa")[0] == []


def test_collect_weighted_prices_bounds_price_range() -> None:
    swaps = [
        _swap("0xaaa", "0xccc", amount0_in=0, amount0_out=1, amount1_in=2_000_000_000, amount1_out=0),  # > 1e9
        _swap("0xaaa", "0xccc", amount0_in=0, amount0_out=1, amount1_in=0, amount1_out=0),  # no price
    ]
    assert _collect_weighted_prices(swaps, "0xaaa")[0] == []


def test_collect_weighted_prices_bad_amounts_skipped() -> None:
    swaps = [
        _swap("0xaaa", "0xccc", amount0_in="junk", amount0_out=0, amount1_in=0, amount1_out=200),
        _swap("0xaaa", "0xccc", amount0_in=100, amount0_out=0, amount1_in=0, amount1_out=200),
    ]
    prices, _ = _collect_weighted_prices(swaps, "0xaaa")
    assert prices == [(2.0, 100.0)]


def test_collect_weighted_prices_handles_numeric_strings() -> None:
    swaps = [_swap("0xaaa", "0xccc", amount0_in="100", amount0_out="0", amount1_in="0", amount1_out="200")]
    assert _collect_weighted_prices(swaps, "0xaaa")[0] == [(2.0, 100.0)]


def test_collect_weighted_prices_empty_and_missing_token_fields() -> None:
    assert _collect_weighted_prices([], "0xaaa") == ([], {})
    assert _collect_weighted_prices([{"token0": "0xaaa"}], "0xaaa") == ([], {})


# ── constants ──────────────────────────────────────────────────────────────


def test_ttl_constants() -> None:
    assert _PRICE_TTL == 300
    assert _MEM_TTL == 60
    assert _MEM_MAX == 500
    assert _NATIVE_TTL == 300


def test_wrapped_native_map_known_entries() -> None:
    assert _WRAPPED_NATIVE["ethereum"] == "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    assert _WRAPPED_NATIVE["bsc"] == "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
    assert _WRAPPED_NATIVE["solana"] == "So11111111111111111111111111111111111111112"
    assert _WRAPPED_NATIVE["base"] == "0x4200000000000000000000000000000000000006"


def test_stablecoin_allowlist_known_entries() -> None:
    # ethereum USDC / USDT / DAI
    assert "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48" in _STABLECOINS
    assert "0xdac17f958d2ee523a2206206994597c13d831ec7" in _STABLECOINS
    assert "0x6b175474e89094c44da98b954eedeac495271d0f" in _STABLECOINS
    # solana USDC / USDT
    assert "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" in _STABLECOINS
    assert "Es9vMFrzaCERmJfrF4H2FYD4KjNkEN6kiFnSkHM6hTzM" in _STABLECOINS


def test_fallback_decimals_spec_values() -> None:
    # Spec: ETH=18, USDC=6, USDT=6, SOL=9
    assert _FALLBACK_DECIMALS["solana"]["So11111111111111111111111111111111111111112"] == 9  # WSOL
    assert _FALLBACK_DECIMALS["solana"]["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"] == 6  # USDC
    assert _FALLBACK_DECIMALS["solana"]["Es9vMFrzaCERmJfrF4H2FYD4KjNkEN6kiFnSkHM6hTzM"] == 6  # USDT
    assert _FALLBACK_DECIMALS["ethereum"]["0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"] == 18  # WETH
    assert _FALLBACK_DECIMALS["ethereum"]["0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"] == 18  # ETH alias
    assert _FALLBACK_DECIMALS["bsc"]["0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"] == 18  # WBNB
    assert _FALLBACK_DECIMALS["base"]["0x4200000000000000000000000000000000000006"] == 18  # WETH
