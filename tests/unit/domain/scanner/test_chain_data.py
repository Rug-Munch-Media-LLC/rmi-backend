"""Unit tests for the Chain Data Access Layer (_chain_data.py).

The module is the CONTRACT for chain→table→column mapping: these tests pin
the mapping so a schema change or new chain fails loudly here instead of
silently querying the wrong table in an engine.
"""

from __future__ import annotations

import pytest

from app.domains.scanner.engines._chain_data import (
    EVM_CHAINS,
    SOLANA_CHAINS,
    TRON_CHAINS,
    get_chain_spec,
    normalize_addr,
)


class TestGetChainSpec:
    def test_ethereum_uses_evm_transfers(self):
        spec = get_chain_spec("ethereum")
        assert spec.chain == "ethereum"
        assert spec.transfers_table == "wallet_memory.evm_transfers"
        assert spec.dex_table == "wallet_memory.dex_events"
        assert spec.addr_col == "token_address"
        assert spec.from_col == "from_address"
        assert spec.to_col == "to_address"
        assert spec.amount_expr == "toFloat64OrZero(value)"
        assert spec.ts_col == "block_timestamp"
        assert spec.tx_hash_col == "tx_hash"
        assert spec.block_col == "block_number"
        assert spec.chain_filter == "AND chain = %(chain)s"
        assert spec.is_solana is False

    def test_solana_uses_spl_balance_changes(self):
        spec = get_chain_spec("solana")
        assert spec.chain == "solana"
        assert spec.is_solana is True
        assert spec.transfers_table == "wallet_memory.solana_spl_balance_changes"
        assert spec.dex_table == "wallet_memory.dex_events"
        assert spec.addr_col == "mint"
        assert spec.from_col == "owner"
        assert spec.to_col == "owner"
        assert spec.amount_expr == "toFloat64OrZero(delta)"
        assert spec.ts_col == "block_timestamp"
        assert spec.tx_hash_col == "tx_hash"
        assert spec.block_col == "slot"
        assert spec.chain_filter == ""  # table is chain-specific

    def test_base_uses_evm_transfers(self):
        spec = get_chain_spec("base")
        assert spec.chain == "base"
        assert spec.transfers_table == "wallet_memory.evm_transfers"
        assert spec.is_solana is False
        assert spec.chain_filter == "AND chain = %(chain)s"

    def test_tron_has_no_clickhouse_transfers_table(self):
        spec = get_chain_spec("tron")
        assert spec.chain == "tron"
        assert spec.is_solana is False
        assert spec.transfers_table == ""
        assert spec.has_transfers_table is False
        assert spec.dex_table == "wallet_memory.dex_events"

    def test_svm_alias_resolves_to_solana(self):
        spec = get_chain_spec("svm")
        assert spec.chain == "solana"
        assert spec.is_solana is True

    def test_case_and_whitespace_insensitive(self):
        assert get_chain_spec("  Solana ").chain == "solana"
        assert get_chain_spec("ETHEREUM").chain == "ethereum"
        assert get_chain_spec("Base").chain == "base"

    @pytest.mark.parametrize("chain", sorted(EVM_CHAINS))
    def test_every_evm_chain_maps_to_evm_transfers(self, chain):
        spec = get_chain_spec(chain)
        assert spec.transfers_table == "wallet_memory.evm_transfers"
        assert spec.is_solana is False
        assert spec.chain == chain

    @pytest.mark.parametrize("chain", sorted(SOLANA_CHAINS))
    def test_every_solana_chain_maps_to_spl_table(self, chain):
        spec = get_chain_spec(chain)
        assert spec.transfers_table == "wallet_memory.solana_spl_balance_changes"
        assert spec.is_solana is True

    @pytest.mark.parametrize("chain", sorted(TRON_CHAINS))
    def test_every_tron_chain_has_no_transfers_table(self, chain):
        assert get_chain_spec(chain).has_transfers_table is False

    @pytest.mark.parametrize("bad", ["", "   ", "sol", "evm", "bitcoin", "SOLANA-X", "eth2"])
    def test_unknown_chain_raises_valueerror(self, bad):
        with pytest.raises(ValueError, match="Unknown chain"):
            get_chain_spec(bad)

    def test_none_chain_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown chain"):
            get_chain_spec(None)  # type: ignore[arg-type]

    def test_spec_is_frozen(self):
        spec = get_chain_spec("ethereum")
        with pytest.raises(AttributeError):
            spec.transfers_table = "wallet_memory.evil"  # type: ignore[misc]

    def test_specs_are_cached_equal_per_chain(self):
        assert get_chain_spec("ethereum") == get_chain_spec("ethereum")
        assert get_chain_spec("solana") != get_chain_spec("ethereum")


class TestNormalizeAddr:
    def test_solana_case_preserved(self):
        addr = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        assert normalize_addr(addr, "solana") == addr

    def test_solana_mixed_case_untouched(self):
        addr = "So11111111111111111111111111111111111111112"
        assert normalize_addr(addr, "solana") == addr

    def test_evm_lowercased(self):
        assert normalize_addr("0xAbC0000000000000000000000000000000000001", "ethereum") == (
            "0xabc0000000000000000000000000000000000001"
        )

    def test_base_lowercased(self):
        assert normalize_addr("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "base") == (
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
        )

    def test_tron_lowercased_per_convention(self):
        assert normalize_addr("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "tron") == ("tr7nhqjekqxgtci8q8zy4pl8otszgjlj6t")

    def test_whitespace_stripped(self):
        assert normalize_addr("  0xABC  ", "ethereum") == "0xabc"
        assert normalize_addr("  EPjFWdd5  ", "solana") == "EPjFWdd5"

    def test_empty_and_none_safe(self):
        assert normalize_addr("", "ethereum") == ""
        assert normalize_addr(None, "solana") == ""  # type: ignore[arg-type]

    def test_unknown_chain_raises(self):
        with pytest.raises(ValueError, match="Unknown chain"):
            normalize_addr("0xabc", "notachain")
