"""Scanner data-access layer — historical / backup token data lookup.

Part 2 — B2 backup data access options (Scanner)

Provides a uniform ``lookup_historical`` entry point that dispatches to the
configured data access mode (see ``app.domains.scanner.config``):

  * ``clickhouse`` — query the chain-indexer ClickHouse tables (fastest,
    already indexed; requires the user running our indexer).
  * ``b2_parquet`` — query historical chain-data parquet on Backblaze B2
    (rclone download + DuckDB; free, no RPC archive node needed).
  * ``rpc`` — live RPC queries (always works, rate-limited).

In ``auto`` mode the resolution chain is: ClickHouse → B2 parquet → RPC.

Every lookup is fail-open: an unavailable backend raises
``DataAccessUnavailable`` which callers should treat as "use RPC instead",
never as a scan failure.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from app.domains.scanner.config import (
    B2_BUCKET,
    B2_PATH_PREFIX,
    B2_REMOTE,
    DataAccessMode,
    resolve_data_access_mode,
)
from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)


class DataAccessUnavailable(RuntimeError):
    """Raised when a data-access backend cannot serve the request."""


# ═══════════════════════════════════════════════════════════════════════
# ClickHouse access
# ═══════════════════════════════════════════════════════════════════════


async def _lookup_clickhouse(address: str, chain: str, query: str, limit: int = 50) -> dict[str, Any]:
    """Query the chain-indexer ClickHouse tables for *address*.

    Uses the same lazy client as the DataBus chain-indexer provider. Only
    safe, parameterized ``SELECT`` queries against chain tables are allowed;
    callers pick a concrete query (never raw user input).
    """
    try:
        from app.domains.databus.providers.chains.lite_chain_indexer import _get_ch_client
        from app.domains.scanner.engines._common import run_sync
    except ImportError as exc:
        raise DataAccessUnavailable("clickhouse_import_missing") from exc

    client = _get_ch_client()
    if client is None:
        raise DataAccessUnavailable("clickhouse_unavailable")

    try:
        rows = await run_sync(client.execute, query, {"address": address, "chain": chain})
    except Exception as exc:
        raise DataAccessUnavailable(f"clickhouse_query_failed: {exc}") from exc

    return {
        "backend": "clickhouse",
        "address": address,
        "chain": chain,
        "row_count": len(rows) if rows else 0,
        "rows": [list(r) for r in (rows or [])][:limit],
        "query": query,
    }


async def clickhouse_token_history(address: str, chain: str, limit: int = 50) -> dict[str, Any]:
    """Return recent dex_events for *address* from ClickHouse (Option 2).

    Queries ``wallet_memory.dex_events`` — the address is matched against
    ``token0`` / ``token1`` (the current schema; the old ``base_address`` /
    ``quote_address`` columns were removed in the chain-indexer schema
    drift, and the drifted ``token_address`` column no longer exists).
    """
    query = (
        "SELECT block_timestamp, dex, token0, token1, amount0, amount1, tx_hash "
        "FROM wallet_memory.dex_events "
        "WHERE chain = {chain:String} "
        "AND (lower(token0) = lower({address:String}) OR lower(token1) = lower({address:String})) "
        "ORDER BY block_timestamp DESC LIMIT {limit:UInt32}"
    )
    return await _lookup_clickhouse(address, chain, query, limit)


# ═══════════════════════════════════════════════════════════════════════
# B2 parquet access (Option 1)
# ═══════════════════════════════════════════════════════════════════════


def _rclone_copy(remote_path: str, dest_dir: Path) -> int:
    """Download+decrypt *remote_path* into *dest_dir*; return file count."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rclone",
        "copy",
        remote_path,
        str(dest_dir),
        "--include",
        "*.parquet",
        "--transfers",
        "4",
        "--no-update-modtime",
    ]
    env = dict(os.environ)
    conf = os.path.expanduser("~/.config/rclone/rclone.conf")
    if Path(conf).exists():
        env["RCLONE_CONFIG"] = conf
    try:
        import subprocess

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, check=False, shell=False)
    except FileNotFoundError as exc:
        raise DataAccessUnavailable("rclone_not_installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise DataAccessUnavailable("rclone_timeout") from exc
    if proc.returncode != 0:
        raise DataAccessUnavailable(f"rclone_failed: {proc.stderr.strip()[:300]}")
    files = sorted(p for p in dest_dir.rglob("*.parquet") if p.is_file())
    return len(files)


async def b2_parquet_lookup(
    address: str,
    chain: str,
    date: str | None = None,
    hour: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Query historical chain-data parquet on B2 for *address* (Option 1).

    Downloads the ``{chain}/{date}/{hour}`` prefix through rclone (the
    ``b2-crypt`` remote is content-encrypted, so DuckDB httpfs cannot read
    it directly) and runs DuckDB over the local parquet files — mirroring
    the RAG ``chain_parquet`` access path. Fails open to
    ``DataAccessUnavailable`` so callers fall through to RPC.
    """
    parts = [B2_PATH_PREFIX, chain]
    if date:
        d_parts = date.split("-")
        if len(d_parts) == 3:
            parts += [d_parts[0], d_parts[1], d_parts[2]]
            if hour:
                parts.append(hour.zfill(2))
    remote = f"{B2_REMOTE}:{B2_BUCKET}/{'/'.join(parts)}"

    tmp = tempfile.TemporaryDirectory(prefix="scanner-b2-")
    try:
        n = await __import__("asyncio").to_thread(_rclone_copy, remote, Path(tmp.name))
        if n == 0:
            raise DataAccessUnavailable("b2_no_parquet_files")

        from duckdb import connect

        con = connect(":memory:")
        files = sorted(p for p in Path(tmp.name).rglob("*.parquet") if p.is_file())
        quoted = ", ".join("'" + str(p).replace("'", "''") + "'" for p in files)
        try:
            # `quoted` is a list of quote-escaped filesystem paths (not bindable in read_parquet).
            con.execute("CREATE OR REPLACE VIEW chain_events AS SELECT * FROM read_parquet([" + quoted + "])")
        except Exception:
            raise DataAccessUnavailable("b2_parquet_unreadable") from None

        rows = con.execute(
            "SELECT * FROM chain_events "
            "WHERE lower(token_address) = lower(?) "
            "OR lower(from_address) = lower(?) "
            "OR lower(to_address) = lower(?) "
            "LIMIT ?",
            [address, address, address, limit],
        ).fetchall()
        columns = [d[0] for d in con.description] if con.description else []
        return {
            "backend": "b2_parquet",
            "address": address,
            "chain": chain,
            "date": date,
            "hour": hour,
            "files_downloaded": n,
            "row_count": len(rows),
            "columns": columns,
            "rows": [list(r) for r in rows],
        }
    except DataAccessUnavailable:
        raise
    except Exception as exc:
        raise DataAccessUnavailable(f"b2_lookup_failed: {exc}") from exc
    finally:
        tmp.cleanup()


# ═══════════════════════════════════════════════════════════════════════
# RPC access (default / Option 3)
# ═══════════════════════════════════════════════════════════════════════


async def rpc_lookup(address: str, chain: str, query: str, limit: int = 50) -> dict[str, Any]:
    """Live RPC lookup for *address* via the shared RPC router (default).

    ``query`` is one of the router-supported methods (e.g.
    ``eth_getBalance``, ``eth_getCode``); returns a best-effort result dict
    or raises ``DataAccessUnavailable`` when the RPC router has no healthy
    endpoint for the chain.
    """
    try:
        from app.domains.scanner.engines.rpc_router import get_rpc_router
    except ImportError as exc:
        raise DataAccessUnavailable("rpc_router_import_missing") from exc

    router = get_rpc_router()
    try:
        result = await router.call(chain, query, [address])
    except Exception as exc:
        raise DataAccessUnavailable(f"rpc_call_failed: {exc}") from exc
    return {
        "backend": "rpc",
        "address": address,
        "chain": chain,
        "method": query,
        "result": result,
    }


# ═══════════════════════════════════════════════════════════════════════
# Dispatcher
# ═══════════════════════════════════════════════════════════════════════


async def lookup_historical(
    address: str,
    chain: str,
    query: str = "dex_history",
    *,
    mode: DataAccessMode | None = None,
    date: str | None = None,
    hour: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Fetch historical token data using the configured access mode.

    In ``auto`` mode the chain is: ClickHouse → B2 parquet → RPC. Every
    backend is fail-open: an unavailable backend falls through to the next
    in the chain, so this function always returns a result dict with a
    ``backend`` field naming the winner (never raises for a data miss).
    """
    mode = mode or resolve_data_access_mode()
    chain_lower = (chain or "").lower()

    # Build the ordered attempt list:
    #   explicit mode → [mode, rpc]
    #   auto          → [clickhouse?, b2?, rpc]
    attempts: list[str] = []
    if mode == "auto":
        if _auto_prefer_clickhouse():
            attempts.append("clickhouse")
        if _auto_prefer_b2():
            attempts.append("b2_parquet")
        attempts.append("rpc")
    else:
        attempts = [str(mode), "rpc"]

    for backend in attempts:
        try:
            if backend == "clickhouse":
                return await clickhouse_token_history(address, chain_lower, limit)
            if backend == "b2_parquet":
                return await b2_parquet_lookup(address, chain_lower, date=date, hour=hour, limit=limit)
            if backend == "rpc":
                return await rpc_lookup(address, chain_lower, query, limit)
        except DataAccessUnavailable as exc:
            logger.debug("data_access_backend_miss", backend=backend, error=str(exc))
            continue

    return {
        "backend": "unavailable",
        "address": address,
        "chain": chain_lower,
        "rows": [],
        "error": "no data access backend available",
    }


def _auto_prefer_clickhouse() -> bool:
    from app.domains.scanner.config import _clickhouse_configured

    return _clickhouse_configured()


def _auto_prefer_b2() -> bool:
    from app.domains.scanner.config import _b2_configured

    return _b2_configured()


__all__ = [
    "DataAccessUnavailable",
    "b2_parquet_lookup",
    "clickhouse_token_history",
    "lookup_historical",
    "rpc_lookup",
]
