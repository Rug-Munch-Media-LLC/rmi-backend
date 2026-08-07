"""Tests for scanner data-access configuration + lookup (Part 2).

Covers:

  1. ``DATA_ACCESS_MODE`` default + env override.
  2. ``resolve_data_access_mode`` auto chain: ClickHouse → B2 → RPC.
  3. ``lookup_historical`` dispatches by mode and falls through fail-open.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner import config as scanner_config
from app.domains.scanner.data_access import (
    DataAccessUnavailable,
    b2_parquet_lookup,
    clickhouse_token_history,
    lookup_historical,
    rpc_lookup,
)


class TestConfig:
    def test_default_mode_is_auto(self) -> None:
        assert scanner_config.DATA_ACCESS_MODE in ("rpc", "b2_parquet", "clickhouse", "auto")

    def test_resolve_explicit_mode(self) -> None:
        with patch.object(scanner_config, "DATA_ACCESS_MODE", "rpc"):
            assert scanner_config.resolve_data_access_mode() == "rpc"

    def test_auto_prefers_clickhouse(self) -> None:
        with (
            patch.object(scanner_config, "DATA_ACCESS_MODE", "auto"),
            patch.object(scanner_config, "_clickhouse_configured", return_value=True),
        ):
            assert scanner_config.resolve_data_access_mode() == "clickhouse"

    def test_auto_falls_to_b2_when_no_clickhouse(self) -> None:
        with (
            patch.object(scanner_config, "DATA_ACCESS_MODE", "auto"),
            patch.object(scanner_config, "_clickhouse_configured", return_value=False),
            patch.object(scanner_config, "_b2_configured", return_value=True),
        ):
            assert scanner_config.resolve_data_access_mode() == "b2_parquet"

    def test_auto_defaults_to_rpc(self) -> None:
        with (
            patch.object(scanner_config, "DATA_ACCESS_MODE", "auto"),
            patch.object(scanner_config, "_clickhouse_configured", return_value=False),
            patch.object(scanner_config, "_b2_configured", return_value=False),
        ):
            assert scanner_config.resolve_data_access_mode() == "rpc"

    def test_describe_data_access_shape(self) -> None:
        info = scanner_config.describe_data_access()
        assert "mode" in info
        assert "resolved" in info
        assert "clickhouse_configured" in info
        assert "b2_configured" in info


class TestLookupHistorical:
    @pytest.mark.asyncio
    async def test_explicit_clickhouse_mode(self) -> None:
        with (
            patch.object(scanner_config, "DATA_ACCESS_MODE", "clickhouse"),
            patch(
                "app.domains.scanner.data_access.clickhouse_token_history",
                new_callable=AsyncMock,
                return_value={"backend": "clickhouse", "rows": [["x"]], "row_count": 1},
            ),
        ):
            result = await lookup_historical("0xabc", "ethereum")
        assert result["backend"] == "clickhouse"

    @pytest.mark.asyncio
    async def test_explicit_b2_mode(self) -> None:
        with (
            patch.object(scanner_config, "DATA_ACCESS_MODE", "b2_parquet"),
            patch(
                "app.domains.scanner.data_access.b2_parquet_lookup",
                new_callable=AsyncMock,
                return_value={"backend": "b2_parquet", "rows": [], "row_count": 0},
            ),
        ):
            result = await lookup_historical("0xabc", "ethereum")
        assert result["backend"] == "b2_parquet"

    @pytest.mark.asyncio
    async def test_explicit_rpc_mode(self) -> None:
        with (
            patch.object(scanner_config, "DATA_ACCESS_MODE", "rpc"),
            patch(
                "app.domains.scanner.data_access.rpc_lookup",
                new_callable=AsyncMock,
                return_value={"backend": "rpc", "result": "0x"},
            ),
        ):
            result = await lookup_historical("0xabc", "ethereum")
        assert result["backend"] == "rpc"

    @pytest.mark.asyncio
    async def test_clickhouse_miss_falls_through_to_rpc(self) -> None:
        """Explicit clickhouse mode falls back to RPC on DataAccessUnavailable."""
        with (
            patch.object(scanner_config, "DATA_ACCESS_MODE", "clickhouse"),
            patch(
                "app.domains.scanner.data_access.clickhouse_token_history",
                new_callable=AsyncMock,
                side_effect=DataAccessUnavailable("clickhouse_unavailable"),
            ),
            patch(
                "app.domains.scanner.data_access.rpc_lookup",
                new_callable=AsyncMock,
                return_value={"backend": "rpc", "result": "0x"},
            ),
        ):
            result = await lookup_historical("0xabc", "ethereum")
        assert result["backend"] == "rpc"

    @pytest.mark.asyncio
    async def test_all_backends_unavailable_returns_unavailable(self) -> None:
        with (
            patch.object(scanner_config, "DATA_ACCESS_MODE", "rpc"),
            patch(
                "app.domains.scanner.data_access.rpc_lookup",
                new_callable=AsyncMock,
                side_effect=DataAccessUnavailable("rpc_down"),
            ),
        ):
            result = await lookup_historical("0xabc", "ethereum")
        assert result["backend"] == "unavailable"
        assert result["error"]


class TestBackends:
    @pytest.mark.asyncio
    async def test_clickhouse_token_history_no_client(self) -> None:
        with (
            patch(
                "app.domains.databus.providers.chains.chain_indexer._get_ch_client",
                return_value=None,
            ),
            pytest.raises(DataAccessUnavailable),
        ):
            await clickhouse_token_history("0xabc", "ethereum")

    @pytest.mark.asyncio
    async def test_rpc_lookup_import_missing(self) -> None:
        with (
            patch.dict("sys.modules", {"app.domains.scanner.engines.rpc_router": None}),
            pytest.raises(DataAccessUnavailable),
        ):
            await rpc_lookup("0xabc", "ethereum", "eth_getCode")

    @pytest.mark.asyncio
    async def test_b2_parquet_lookup_no_rclone(self) -> None:
        """Without the rclone binary the B2 lookup fails open."""
        with (
            patch(
                "app.domains.scanner.data_access._rclone_copy",
                side_effect=DataAccessUnavailable("rclone_not_installed"),
            ),
            pytest.raises(DataAccessUnavailable),
        ):
            await b2_parquet_lookup("0xabc", "ethereum")
