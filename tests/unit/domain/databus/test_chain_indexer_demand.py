"""Tests for lazy-indexing demand signaling in the lite_chain_indexer provider.

The scanner touches ``{INDEXER_DEMAND_DIR}/{chain}/{event_type}`` whenever
it queries dex_events / deployer_events on a chain so the lite-chain-indexer
can enable per-chain event indexing on demand (lazy indexing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


class TestSignalIndexerDemand:
    """_signal_indexer_demand file-touch behavior."""

    def test_touches_demand_file(self, monkeypatch, tmp_path: Path):
        from app.domains.databus.providers.chains.lite_chain_indexer import _signal_indexer_demand

        demand_dir = tmp_path / "indexer-demand"
        monkeypatch.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._INDEXER_DEMAND_DIR",
            str(demand_dir),
        )

        _signal_indexer_demand("Scroll", "dex")

        assert (demand_dir / "scroll" / "dex").is_file()
        # Idempotent: touching again does not raise
        _signal_indexer_demand("scroll", "dex")
        assert (demand_dir / "scroll" / "dex").is_file()

    def test_deployer_signal(self, monkeypatch, tmp_path: Path):
        from app.domains.databus.providers.chains.lite_chain_indexer import _signal_indexer_demand

        demand_dir = tmp_path / "indexer-demand"
        monkeypatch.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._INDEXER_DEMAND_DIR",
            str(demand_dir),
        )

        _signal_indexer_demand("scroll", "deployer")
        assert (demand_dir / "scroll" / "deployer").is_file()

    def test_empty_args_noop(self, monkeypatch, tmp_path: Path):
        from app.domains.databus.providers.chains.lite_chain_indexer import _signal_indexer_demand

        demand_dir = tmp_path / "indexer-demand"
        monkeypatch.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._INDEXER_DEMAND_DIR",
            str(demand_dir),
        )

        _signal_indexer_demand("", "dex")
        _signal_indexer_demand("scroll", "")
        assert not demand_dir.exists()


class TestProviderDemandSignals:
    """Provider query functions signal demand for their event type."""

    @pytest.mark.asyncio
    async def test_dex_events_signals_demand(self, monkeypatch, tmp_path: Path):
        from app.domains.databus.providers.chains import lite_chain_indexer

        demand_dir = tmp_path / "indexer-demand"
        monkeypatch.setattr(lite_chain_indexer, "_INDEXER_DEMAND_DIR", str(demand_dir))
        monkeypatch.setattr(lite_chain_indexer, "_get_ch_client", lambda: None)

        # No rows returned (ClickHouse unavailable), but the demand signal
        # must still be recorded before the query runs.
        out = await lite_chain_indexer._lite_chain_indexer_dex_events(token_address="0xabc", chain="scroll")
        assert out is None
        assert (demand_dir / "scroll" / "dex").is_file()

    @pytest.mark.asyncio
    async def test_deployer_history_signals_demand(self, monkeypatch, tmp_path: Path):
        from app.domains.databus.providers.chains import lite_chain_indexer

        demand_dir = tmp_path / "indexer-demand"
        monkeypatch.setattr(lite_chain_indexer, "_INDEXER_DEMAND_DIR", str(demand_dir))
        monkeypatch.setattr(lite_chain_indexer, "_get_ch_client", lambda: None)

        out = await lite_chain_indexer._lite_chain_indexer_deployer_history(address="0xdeployer", chain="scroll")
        assert out is None
        assert (demand_dir / "scroll" / "deployer").is_file()

    @pytest.mark.asyncio
    async def test_deployer_history_without_chain_no_signal(self, monkeypatch, tmp_path: Path):
        from app.domains.databus.providers.chains import lite_chain_indexer

        demand_dir = tmp_path / "indexer-demand"
        monkeypatch.setattr(lite_chain_indexer, "_INDEXER_DEMAND_DIR", str(demand_dir))
        monkeypatch.setattr(lite_chain_indexer, "_get_ch_client", lambda: None)

        # Chain-less deployer query spans every chain — cannot attribute
        # demand to a single chain, so no signal file is written.
        out = await lite_chain_indexer._lite_chain_indexer_deployer_history(address="0xdeployer")
        assert out is None
        assert not demand_dir.exists()

    @pytest.mark.asyncio
    async def test_token_transfers_no_demand_signal(self, monkeypatch, tmp_path: Path):
        from app.domains.databus.providers.chains import lite_chain_indexer

        demand_dir = tmp_path / "indexer-demand"
        monkeypatch.setattr(lite_chain_indexer, "_INDEXER_DEMAND_DIR", str(demand_dir))
        monkeypatch.setattr(lite_chain_indexer, "_get_ch_client", lambda: None)

        # Transfers are always indexed — no lazy demand signal needed.
        out = await lite_chain_indexer._lite_chain_indexer_token_transfers(token_address="0xabc", chain="scroll")
        assert out is None
        assert not demand_dir.exists()
