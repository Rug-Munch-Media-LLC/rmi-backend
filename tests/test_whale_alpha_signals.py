"""Tests for whale-feed alpha signals (app.routers.v1.whale_enrichment.build_alpha_signal)."""

from __future__ import annotations

from app.routers.v1.whale_enrichment import build_alpha_signal, is_sell_direction

USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"  # Solana USDC (stable map entry)
TOKEN = "0xtoken1234567890abcdef1234567890123456789"


def _item(usd: float, *, stable: str = USDC, asset: str = TOKEN, sym: str = "XYZ") -> dict:
    return {
        "sender": "0xwhale",
        "usd_value": usd,
        "token0": stable,
        "token1": asset,
        "symbol0": "USDC",
        "symbol1": sym,
        "pair_address": "0xpair",
    }


def _swap(stable_in: float = 0, stable_out: float = 0, asset: str = TOKEN) -> dict:
    return {
        "chain": "solana",
        "ts": 1,
        "token0": USDC,
        "token1": asset,
        "a0_in": stable_in,
        "a1_in": 0,
        "a0_out": stable_out,
        "a1_out": 0,
    }


_SMART = {"round_trips": 6, "wins": 5, "win_rate": 0.833, "realized_pnl_usd": 42_000.0, "is_smart_money": True}


class TestLargeSwap:
    def test_unusual_size_flagged(self):
        item = _item(500_000.0)
        ctx = {"avg_trade_usd": 50_000.0, "direction": "accumulating"}
        sig = build_alpha_signal(item, [], ctx, _SMART)
        assert sig is not None
        assert sig["type"] == "large_swap"
        assert "10.0x" in sig["why_notable"]
        assert sig["track_record"]["is_smart_money"] is True

    def test_normal_size_not_flagged(self):
        item = _item(10_000.0)
        ctx = {"avg_trade_usd": 50_000.0, "direction": "mixed"}
        assert build_alpha_signal(item, [], ctx, _SMART) is None


class TestNewTokenEntry:
    def test_first_entry_flagged(self):
        item = _item(8_000.0)  # normal size — isolates the new-entry signal
        # History only ever touched another token.
        swaps = [_swap_other()]
        ctx = {"avg_trade_usd": 10_000.0, "direction": "accumulating"}
        sig = build_alpha_signal(item, swaps, ctx, _SMART)
        assert sig is not None
        assert sig["type"] == "new_token_entry"
        assert sig["token"] == "XYZ"

    def test_known_token_not_flagged(self):
        item = _item(80_000.0)
        swaps = [_swap_other(), _swap_known()]
        ctx = {"avg_trade_usd": 100_000.0, "direction": "mixed"}
        assert build_alpha_signal(item, swaps, ctx, _SMART) is None


class TestExitAfterPump:
    def test_profitable_sell_flagged(self):
        item = _item(120_000.0)
        swaps = [_swap_known(), _swap_sell()]
        ctx = {"avg_trade_usd": 100_000.0, "direction": "distributing"}
        sig = build_alpha_signal(item, swaps, ctx, _SMART)
        assert sig is not None
        assert sig["type"] == "exit_after_pump"
        assert "$42,000" in sig["why_notable"]


class TestIsSellDirection:
    def test_stable_in_means_sell(self):
        assert is_sell_direction([_swap_sell()], _item(1)) is True

    def test_stable_out_means_buy(self):
        assert is_sell_direction([_swap_buy()], _item(1)) is False

    def test_no_history_defaults_buy(self):
        assert is_sell_direction([], _item(1)) is False


def _swap_other() -> dict:
    return _swap(stable_out=1.0, asset="0xothertoken000000000000000000000000000000")


def _swap_known() -> dict:
    return _swap(stable_out=1.0, asset=TOKEN)


def _swap_sell() -> dict:
    return _swap(stable_in=1.0, asset=TOKEN)


def _swap_buy() -> dict:
    return _swap(stable_out=1.0, asset=TOKEN)
