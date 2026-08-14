#!/usr/bin/env python3
"""dlt ELT ingestion wrapper for crypto data providers.

Small, config-driven dlt pipeline that pulls REST data from key-less crypto
data providers and loads it into a destination. The default destination is
``filesystem`` (Parquet files) so the script runs end-to-end without a live
ClickHouse. ``duckdb`` and ``clickhouse`` are also supported.

Providers (all reachable without an API key; optional key for CoinGecko):

* DexScreener token pairs   -- GET /latest/dex/search?q=<query>
* DexScreener token profiles -- GET /token-profiles/latest/v1
* CoinGecko coin markets    -- GET /api/v3/coins/markets (optional
  ``COINGECKO_API_KEY`` sent as ``x_cg_demo_api_key``)

Usage
-----
    python scripts/dlt_ingest.py --destination filesystem --limit 5
    python scripts/dlt_ingest.py --destination duckdb --limit 20
    python scripts/dlt_ingest.py --destination clickhouse \\
        --clickhouse-dsn clickhouse://user:pass@host:9000/default --limit 100

Destinations
------------
* ``filesystem`` (default): writes Parquet to ``--output-dir`` (defaults to
  ``~/.dlt/data/rmi-backend/<pipeline-name>`` so the git tree stays clean).
* ``duckdb``: writes a local DuckDB file at ``--output-dir/ingest.duckdb``
  (requires ``pip install "dlt[duckdb]"``).
* ``clickhouse``: loads straight into ClickHouse; pass ``--clickhouse-dsn``
  or set ``CLICKHOUSE_DSN`` (requires ``pip install "dlt[clickhouse]"``).

Failure behavior: a failed provider call is logged and skipped (the other
resources still load); a missing ``dlt`` prints install instructions and
exits with code 2.

Dependencies: Python 3.11+ (``from __future__ import annotations``), httpx,
and dlt. Install with ``pip install "dlt[filesystem]"`` (or
``"dlt[clickhouse]"`` for the ClickHouse destination).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator

try:
    import dlt
except ImportError:  # pragma: no cover - exercised on envs without dlt
    dlt = None  # type: ignore[assignment]

DLT_INSTALL_HINT: Final[str] = (
    "dlt is not installed. Install it with one of:\n"
    '  pip install "dlt[filesystem]"   # Parquet/JSONL files (default, no DB needed)\n'
    '  pip install "dlt[clickhouse]"   # ClickHouse destination\n'
    '  pip install "dlt[duckdb]"       # DuckDB destination\n'
    "See https://dlthub.com/docs for details."
)

DESTINATION_EXTRAS: Final[dict[str, str]] = {
    "clickhouse": "dlt[clickhouse]",
    "duckdb": "dlt[duckdb]",
    "filesystem": "dlt[filesystem]",
}

USER_AGENT: Final[str] = "rmi-dlt-ingest/1.0"
HTTP_TIMEOUT: Final[float] = 15.0

logger = logging.getLogger("dlt_ingest")


def _request_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    """GET ``url`` and return parsed JSON, raising ``httpx.HTTPError`` on failure."""
    merged_headers = {"User-Agent": USER_AGENT}
    if headers:
        merged_headers.update(headers)
    resp = httpx.get(url, params=params, headers=merged_headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


if dlt is not None:

    @dlt.resource(name="dexscreener_pairs", write_disposition="replace", primary_key="pairAddress")
    def dexscreener_pairs(query: str = "pepe", limit: int | None = None) -> Iterator[dict[str, Any]]:
        """DexScreener token pairs for a search query (no key required)."""
        try:
            payload = _request_json("https://api.dexscreener.com/latest/dex/search", params={"q": query})
            records = payload.get("pairs") or []
            if limit is not None:
                records = records[:limit]
            logger.info("dexscreener_pairs: fetched %d pair(s) for query=%r", len(records), query)
            yield from records
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("dexscreener_pairs: provider call failed, skipping: %s", exc)

    @dlt.resource(name="dexscreener_token_profiles", write_disposition="replace", primary_key="tokenAddress")
    def dexscreener_token_profiles(limit: int | None = None) -> Iterator[dict[str, Any]]:
        """Most recent DexScreener token profiles (no key required)."""
        try:
            records = _request_json("https://api.dexscreener.com/token-profiles/latest/v1")
            if limit is not None:
                records = records[:limit]
            logger.info("dexscreener_token_profiles: fetched %d profile(s)", len(records))
            yield from records
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("dexscreener_token_profiles: provider call failed, skipping: %s", exc)

    @dlt.resource(
        name="coingecko_markets",
        write_disposition="replace",
        primary_key="id",
        columns={"roi": {"data_type": "text"}},  # roi is null for all rows; hint avoids type-inference warning
    )
    def coingecko_markets(limit: int | None = None, api_key: str | None = None) -> Iterator[dict[str, Any]]:
        """CoinGecko top markets by market cap (optional demo API key)."""
        per_page = min(limit, 250) if limit else 250
        headers = {"x_cg_demo_api_key": api_key} if api_key else None
        try:
            records = _request_json(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": str(per_page),
                    "page": "1",
                    "sparkline": "false",
                },
                headers=headers,
            )
            logger.info("coingecko_markets: fetched %d coin market(s)", len(records))
            yield from records
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("coingecko_markets: provider call failed, skipping: %s", exc)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="dlt ELT ingestion wrapper for crypto data providers (DexScreener, CoinGecko).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--destination",
        choices=sorted(DESTINATION_EXTRAS),
        default="filesystem",
        help="dlt destination to load into (filesystem writes Parquet, no DB required).",
    )
    parser.add_argument("--pipeline-name", default="dlt_crypto_ingest", help="dlt pipeline name.")
    parser.add_argument("--dataset-name", default="provider_raw", help="dlt dataset/schema name.")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max records per provider resource (pass 0 for no cap).",
    )
    parser.add_argument(
        "--query",
        default="pepe",
        help="DexScreener search query used by the dexscreener_pairs resource.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / ".dlt" / "data" / "rmi-backend",
        help="Output directory for filesystem/duckdb destinations.",
    )
    parser.add_argument(
        "--clickhouse-dsn",
        default=os.environ.get("CLICKHOUSE_DSN"),
        help="ClickHouse DSN (clickhouse://user:pass@host:9000/default). Defaults to CLICKHOUSE_DSN env var.",
    )
    return parser.parse_args(argv)


def _build_destination(args: argparse.Namespace) -> Any:
    """Return a dlt destination factory, raising ``RuntimeError`` with an install hint when unsupported."""
    if args.destination == "filesystem":
        bucket_url = args.output_dir / args.pipeline_name
        bucket_url.mkdir(parents=True, exist_ok=True)
        return dlt.destinations.filesystem(
            bucket_url=f"file://{bucket_url}",
            preferred_loader_file_format="parquet",
        )
    if args.destination == "duckdb":
        args.output_dir.mkdir(parents=True, exist_ok=True)
        return dlt.destinations.duckdb(credentials=str(args.output_dir / "ingest.duckdb"))
    if args.destination == "clickhouse":
        if not args.clickhouse_dsn:
            raise RuntimeError(
                "--clickhouse-dsn (or CLICKHOUSE_DSN env var) is required for the clickhouse destination"
            )
        return dlt.destinations.clickhouse(credentials=args.clickhouse_dsn)
    raise RuntimeError(
        f"unsupported destination {args.destination!r}"
    )  # pragma: no cover - argparse constrains choices


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)

    if dlt is None:
        print(DLT_INSTALL_HINT, file=sys.stderr)
        return 2

    limit = args.limit if args.limit and args.limit > 0 else None

    try:
        destination = _build_destination(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    resources = [
        dexscreener_pairs(query=args.query, limit=limit),
        dexscreener_token_profiles(limit=limit),
        coingecko_markets(limit=limit, api_key=os.environ.get("COINGECKO_API_KEY")),
    ]

    pipeline = dlt.pipeline(
        pipeline_name=args.pipeline_name,
        pipelines_dir=str(Path.home() / ".dlt" / "pipelines"),
        destination=destination,
        dataset_name=args.dataset_name,
    )

    try:
        info = pipeline.run(resources)
    except Exception as exc:  # destination misconfiguration / missing extras
        hint = ""
        try:
            from dlt.common.exceptions import MissingDependencyException

            if isinstance(exc, MissingDependencyException):
                extra = DESTINATION_EXTRAS.get(args.destination, "dlt")
                hint = f'\nInstall the destination extra first: pip install "{extra}"'
        except ImportError:  # pragma: no cover - dlt is imported, so this path is unreachable
            pass
        print(f"error: pipeline run failed for destination {args.destination!r}: {exc}{hint}", file=sys.stderr)
        return 2

    print("\nPipeline run summary:")
    print(str(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
