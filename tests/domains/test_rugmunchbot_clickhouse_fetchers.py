"""Smoke tests for the alpha-channel real-data fetchers (ClickHouse + smart feed).

Covers the three live sources wired into ``fetch_alpha_content``:
  * ``fetch_whale_moves`` — token_transfers rows ≥ $10k, entity-labeled
  * ``fetch_early_whale_entries`` — first-block buyers of fresh tokens, with size
  * ``fetch_smart_money_wallets`` — normalization of the live smart_money_feed

ClickHouse is mocked at the ``_ch_query`` boundary (no driver, no network);
entity labeling and the smart-money feed are patched to keep the smoke run
hermetic. Verifies the fail-open contract: bad rows are skipped, quiet
sources return ``[]``, never raise.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.domains.telegram.rugmunchbot.channels import clickhouse_fetchers

# ── Whale moves ────────────────────────────────────────────────────────────


class TestFetchWhaleMoves:
    async def test_normalizes_rows_with_entity_labels(self) -> None:
        rows = [
            (
                "solana",  # chain_id
                "2026-08-16 10:00:00",  # block_timestamp
                "txhash111",  # transaction_hash
                "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",  # binance (known)
                "11111111111111111111111111111111",  # to
                "So11111111111111111111111111111111111111112",  # token (SOL)
                52_340.0,  # value_decimal (USD)
                "spl",  # transfer_type
            )
        ]
        with (
            patch.object(clickhouse_fetchers, "_ch_query", new=AsyncMock(return_value=rows)),
            patch.object(
                clickhouse_fetchers,
                "_entity_label",
                side_effect=lambda a: "binance (cex_hot_wallet)" if "9WzD" in a else "",
            ),
        ):
            moves = await clickhouse_fetchers.fetch_whale_moves(chain="solana", limit=3)
        assert len(moves) == 1
        move = moves[0]
        assert move["kind"] == "whale_move"
        assert move["token_address"] == "So11111111111111111111111111111111111111112"
        assert move["value_usd"] == 52_340.0
        assert move["title"] == "Whale: $52,340 So1111...1112 moved"
        assert "binance (cex_hot_wallet)" in move["body"]
        assert move["source"] == "token_transfers"

    async def test_skips_rows_without_token_or_value(self) -> None:
        rows = [
            ("solana", "ts", "tx1", "from1", "to1", "", 50_000.0, "spl"),  # no token
            ("solana", "ts", "tx2", "from2", "to2", "tok2", 0.0, "spl"),  # zero value
            ("solana", "ts", "tx3", "from3", "to3", "tok3", 25_000.0, "spl"),
        ]
        with (
            patch.object(clickhouse_fetchers, "_ch_query", new=AsyncMock(return_value=rows)),
            patch.object(clickhouse_fetchers, "_entity_label", return_value=""),
        ):
            moves = await clickhouse_fetchers.fetch_whale_moves(chain="solana", limit=5)
        assert len(moves) == 1
        assert moves[0]["token_address"] == "tok3"

    async def test_empty_on_ch_unavailable(self) -> None:
        with patch.object(clickhouse_fetchers, "_ch_query", new=AsyncMock(return_value=[])):
            assert await clickhouse_fetchers.fetch_whale_moves(chain="solana") == []


# ── Early whale entries ────────────────────────────────────────────────────


class TestFetchEarlyWhaleEntries:
    def _deploys(self) -> list[tuple]:
        return [
            ("deployer1", "tokA", "solana", 1000, "2026-08-16 09:00:00", "deploytx1"),
            ("deployer2", "tokB", "solana", 2000, "2026-08-16 09:05:00", "deploytx2"),
        ]

    def _transfers_tok_a(self) -> list[tuple]:
        # First-block buyers of tokA: deployer itself (skipped), infra (skipped),
        # a real early wallet, and a later wallet inside the window.
        return [
            (1000, "deployer1", "deployer1", "tokA", 0.0, "tx0"),  # self-mint, zero value
            (1001, "w1", "JUP6LkbZbjS1jKKwapdHNy7bKNkD5Ka6TqPnVGFVeYf", "tokA", 12_500.0, "tx1"),  # jupiter (infra)
            (1002, "w2", "earlywallet1", "tokA", 8_900.0, "tx2"),  # early whale
            (1005, "w3", "earlywallet2", "tokA", 6_000.0, "tx3"),  # +5 blocks
        ]

    async def test_surfaces_first_block_buyers_with_size(self) -> None:
        async def fake_ch_query(sql: str, params: dict | None = None) -> list[tuple]:
            if "deployer_events" in sql:
                return self._deploys()
            token = (params or {}).get("token", "")
            return self._transfers_tok_a() if token == "tokA" else []

        # Jupiter V6 is known infrastructure (exchange/DeFi) — excluded.
        jupiter = "JUP6LkbZbjS1jKKwapdHNy7bKNkD5Ka6TqPnVGFVeYf"

        with (
            patch.object(clickhouse_fetchers, "_ch_query", new=AsyncMock(side_effect=fake_ch_query)),
            patch.object(clickhouse_fetchers, "_entity_label", return_value=""),
            patch.object(clickhouse_fetchers, "_is_infrastructure", side_effect=lambda a: a == jupiter),
        ):
            entries = await clickhouse_fetchers.fetch_early_whale_entries(chain="solana", limit=3)
        # deployer self-mint and zero-value rows are dropped; only 2 real
        # wallets (tokB's transfers query returns [] so only tokA contributes).
        assert len(entries) == 2
        first = entries[0]
        assert first["kind"] == "early_entry"
        assert first["wallet"] == "earlywallet1"
        assert first["value_usd"] == 8_900.0
        assert first["block_delta"] == 2
        assert first["source"] == "deployer_events"
        assert "🐣 Early whale" in first["title"]

    async def test_empty_when_no_deploys(self) -> None:
        with (
            patch.object(clickhouse_fetchers, "_ch_query", new=AsyncMock(return_value=[])),
            patch.object(clickhouse_fetchers, "_is_infrastructure", return_value=False),
        ):
            assert await clickhouse_fetchers.fetch_early_whale_entries(chain="solana") == []


# ── Smart money ────────────────────────────────────────────────────────────


class TestFetchSmartMoneyWallets:
    async def test_normalizes_feed_output(self) -> None:
        feed_result = {
            "smart_money_wallets": [
                {
                    "address": "walletA",
                    "signal": "large_holder",
                    "entity": "Wintermute",
                    "entity_type": "market_maker",
                    "estimated_usd": 45_000,
                },
                {"address": "walletB", "signal": "accumulator", "estimated_usd": 12_000},
            ],
            "total_tracked": 2,
        }
        with patch.object(clickhouse_fetchers, "smart_money_feed", new=AsyncMock(return_value=feed_result)):
            wallets = await clickhouse_fetchers.fetch_smart_money_wallets(chain="solana", limit=5)
        assert len(wallets) == 2
        assert wallets[0]["address"] == "walletA"
        assert wallets[0]["label"] == "Wintermute"
        assert wallets[0]["tags"] == ["large_holder", "market_maker"]
        assert wallets[0]["pnl_usd"] == 45_000
        assert wallets[1]["label"] == "accumulator"

    async def test_empty_on_feed_failure(self) -> None:
        with patch.object(clickhouse_fetchers, "smart_money_feed", new=AsyncMock(side_effect=RuntimeError("boom"))):
            assert await clickhouse_fetchers.fetch_smart_money_wallets(chain="solana", limit=5) == []

    async def test_skips_wallets_without_address(self) -> None:
        with patch.object(
            clickhouse_fetchers,
            "smart_money_feed",
            new=AsyncMock(return_value={"smart_money_wallets": [{"signal": "x"}, {"address": "ok", "signal": "y"}]}),
        ):
            wallets = await clickhouse_fetchers.fetch_smart_money_wallets(chain="solana", limit=5)
        assert len(wallets) == 1
        assert wallets[0]["address"] == "ok"
