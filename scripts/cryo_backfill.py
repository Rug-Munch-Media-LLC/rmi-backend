#!/usr/bin/env python3
"""cryo historical on-chain backfill wrapper.

Wraps the ``cryo`` CLI (paradigmxyz/cryo, https://github.com/paradigmxyz/cryo)
to extract EVM datasets (erc20_transfers, native_transfers, transactions,
logs, blocks) as Parquet files and load them into ClickHouse.

Usage
-----
    python scripts/cryo_backfill.py \\
        --dataset erc20_transfers \\
        --start 20000000 --end 20000100 \\
        --rpc-url https://eth.llamarpc.com \\
        --output-dir /tmp/cryo-out \\
        --clickhouse-dsn clickhouse://user:pass@host:9000/default

Two ClickHouse paths are supported:

1. ``--clickhouse-dsn`` (optional): load directly. Prefers ``clickhouse-client``
   (password read from the ``CLICKHOUSE_PASSWORD`` env var so it never appears
   in argv), falling back to ``clickhouse-local`` with the ``remote()`` table
   function. If neither binary is found, the SQL file is still emitted.
2. Always: a ``<output-dir>/<dataset>_clickhouse.sql`` file is written with the
   ``CREATE TABLE`` + ``INSERT`` statements an operator can run by hand.

Dependencies: stdlib only. The ``cryo`` binary is discovered on PATH and is
NOT bundled -- see ``CRYO_INSTALL_HINT``.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Final

CRYO_INSTALL_HINT: Final[str] = (
    "cryo binary not found on PATH.\n"
    "Install it with one of:\n"
    "  cargo install cryo_cli          # Rust CLI (recommended)\n"
    "  pip install cryo                # Python API (no CLI binary)\n"
    "  download a release binary from  https://github.com/paradigmxyz/cryo/releases\n"
    'Then make sure it is on PATH (e.g. export PATH="$HOME/.cargo/bin:$PATH").\n'
    "See https://github.com/paradigmxyz/cryo for details."
)

CLICKHOUSE_CLIENT_HINT: Final[str] = (
    "clickhouse-client not found and clickhouse-local not found; could not load directly. "
    "The SQL file was still written -- run it manually."
)

# cryo parquet/CSV type -> ClickHouse type (binary columns are emitted as hex
# strings via --hex, so they map cleanly to ClickHouse String).
TYPE_MAP: Final[dict[str, str]] = {
    "uint32": "UInt32",
    "uint64": "UInt64",
    "binary": "String",
    "string": "String",
    "float64": "Float64",
    "bool": "Bool",
}


@dataclass(frozen=True)
class DatasetSpec:
    """Schema + MergeTree ordering for one cryo dataset.

    ``columns`` mirrors the DEFAULT output columns of ``cryo help <dataset>``
    (verified against cryo 0.3.2): (column_name, cryo_type).
    ``order_by`` mirrors cryo's documented sorting, used for the MergeTree key.
    """

    columns: tuple[tuple[str, str], ...]
    order_by: tuple[str, ...]


# Verified via `cryo help <dataset>` with cryo 0.3.2 (cryo_cli crate).
DATASETS: Final[dict[str, DatasetSpec]] = {
    "erc20_transfers": DatasetSpec(
        columns=(
            ("block_number", "uint32"),
            ("transaction_index", "uint32"),
            ("log_index", "uint32"),
            ("transaction_hash", "binary"),
            ("erc20", "binary"),
            ("from_address", "binary"),
            ("to_address", "binary"),
            ("value_binary", "binary"),
            ("value_string", "string"),
            ("value_f64", "float64"),
            ("chain_id", "uint64"),
        ),
        order_by=("block_number", "log_index"),
    ),
    "native_transfers": DatasetSpec(
        columns=(
            ("block_number", "uint32"),
            ("block_hash", "binary"),
            ("transaction_index", "uint32"),
            ("transfer_index", "uint32"),
            ("transaction_hash", "binary"),
            ("from_address", "binary"),
            ("to_address", "binary"),
            ("value_binary", "binary"),
            ("value_string", "string"),
            ("value_f64", "float64"),
            ("chain_id", "uint64"),
        ),
        order_by=("block_number", "transaction_index"),
    ),
    "transactions": DatasetSpec(
        columns=(
            ("block_number", "uint32"),
            ("transaction_index", "uint64"),
            ("transaction_hash", "binary"),
            ("nonce", "uint64"),
            ("from_address", "binary"),
            ("to_address", "binary"),
            ("value_binary", "binary"),
            ("value_string", "string"),
            ("value_f64", "float64"),
            ("input", "binary"),
            ("gas_limit", "uint64"),
            ("gas_used", "uint64"),
            ("gas_price", "uint64"),
            ("transaction_type", "uint32"),
            ("max_priority_fee_per_gas", "uint64"),
            ("max_fee_per_gas", "uint64"),
            ("success", "bool"),
            ("n_input_bytes", "uint32"),
            ("n_input_zero_bytes", "uint32"),
            ("n_input_nonzero_bytes", "uint32"),
            ("chain_id", "uint64"),
        ),
        order_by=("block_number", "transaction_index"),
    ),
    "logs": DatasetSpec(
        columns=(
            ("block_number", "uint32"),
            ("transaction_index", "uint32"),
            ("log_index", "uint32"),
            ("transaction_hash", "binary"),
            ("address", "binary"),
            ("topic0", "binary"),
            ("topic1", "binary"),
            ("topic2", "binary"),
            ("topic3", "binary"),
            ("data", "binary"),
            ("chain_id", "uint64"),
        ),
        order_by=("block_number", "log_index"),
    ),
    "blocks": DatasetSpec(
        columns=(
            ("block_number", "uint32"),
            ("block_hash", "binary"),
            ("timestamp", "uint32"),
            ("author", "binary"),
            ("gas_used", "uint64"),
            ("extra_data", "binary"),
            ("base_fee_per_gas", "uint64"),
            ("chain_id", "uint64"),
        ),
        order_by=("block_number",),
    ),
}

DEFAULT_CH_PORT: Final[int] = 9000
DEFAULT_CH_USER: Final[str] = "default"
DEFAULT_CH_DB: Final[str] = "default"


def build_cryo_cmd(
    dataset: str,
    start: int,
    end: int,
    output_dir: str,
    rpc_url: str | None = None,
    *,
    overwrite: bool = False,
) -> list[str]:
    """Build the ``cryo`` subprocess argv (verified against cryo 0.3.2)."""
    cmd: list[str] = [
        "cryo",
        dataset,
        "--blocks",
        f"{start}:{end}",
        "--output-dir",
        output_dir,
        # binary columns -> hex strings, clean for ClickHouse String columns
        "--hex",
    ]
    if rpc_url:
        cmd += ["--rpc", rpc_url]
    if overwrite:
        cmd.append("--overwrite")
    return cmd


def generate_create_table(dataset: str, database: str) -> str:
    """Generate the ClickHouse ``CREATE TABLE`` DDL for a dataset."""
    spec = DATASETS[dataset]
    cols = ",\n".join(f"    `{name}` {TYPE_MAP[ctype]}" for name, ctype in spec.columns)
    order_by = ", ".join(f"`{col}`" for col in spec.order_by)
    return (
        f"CREATE TABLE IF NOT EXISTS `{database}`.`{dataset}`\n(\n{cols}\n)\nENGINE = MergeTree\nORDER BY ({order_by});"
    )


def generate_insert_sql(dataset: str, database: str, output_dir: str) -> str:
    """Generate the ``INSERT ... SELECT * FROM file()`` statement.

    The ``file()`` glob reads every Parquet chunk cryo wrote into
    ``output_dir`` (cryo names them ``<network>__<dataset>__<range>.parquet``).
    """
    abs_dir = Path(output_dir).resolve()
    return f"INSERT INTO `{database}`.`{dataset}`\nSELECT * FROM file('{abs_dir}/*.parquet', 'Parquet');"


def write_sql_file(dataset: str, database: str, output_dir: str) -> Path:
    """Write the operator-runnable SQL file; returns its path."""
    header = (
        "-- Generated by scripts/cryo_backfill.py\n"
        f"-- Dataset: {dataset}  Table: `{database}`.`{dataset}`\n"
        "-- Run with: clickhouse-local --multiquery < this-file\n"
        "--   (or clickhouse-client --multiquery, with the parquet dir visible to the server)\n\n"
    )
    sql = (
        header
        + generate_create_table(dataset, database)
        + "\n\n"
        + generate_insert_sql(dataset, database, output_dir)
        + "\n"
    )
    path = Path(output_dir).resolve() / f"{dataset}_clickhouse.sql"
    path.write_text(sql, encoding="utf-8")
    return path


@dataclass(frozen=True)
class ChDsn:
    """Parsed ClickHouse DSN (clickhouse://user:pass@host:port/db)."""

    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def parse(cls, dsn: str) -> ChDsn:
        parsed = urllib.parse.urlsplit(dsn)
        if parsed.scheme not in ("clickhouse", "http", "https"):
            raise ValueError(f"unsupported ClickHouse DSN scheme: {parsed.scheme!r}")
        host = parsed.hostname or "localhost"
        port = parsed.port or DEFAULT_CH_PORT
        user = parsed.username or DEFAULT_CH_USER
        password = parsed.password or ""
        database = (parsed.path or "").strip("/") or DEFAULT_CH_DB
        return cls(host=host, port=port, user=user, password=password, database=database)


def clickhouse_client_available() -> str | None:
    return shutil.which("clickhouse-client")


def clickhouse_local_available() -> str | None:
    return shutil.which("clickhouse-local")


def _client_major_version(client: str) -> int | None:
    """Return the clickhouse-client major version, or None if undetectable."""
    try:
        res = subprocess.run(  # noqa: S603 — static argv [client, --version], shell=False
            [client, "--version"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for token in res.stdout.split():
        major = token.split(".")[0]
        if major.isdigit():
            return int(major)
    return None


def _client_honors_password_env(client: str) -> bool:
    """True when the clickhouse-client honors the CLICKHOUSE_PASSWORD env var.

    Older clients (pre-~21.x, e.g. the 18.x package on some hosts) only read
    ``--password`` from argv. Env-var auth is preferred (password never shows
    in the process list); fall back to argv for those clients.
    """
    major = _client_major_version(client)
    return major is not None and major >= 21


def _client_supports_parquet(client: str) -> bool:
    """True when clickhouse-client can ingest the Parquet wire format.

    Parquet input format support landed in ClickHouse ~22.x; older clients
    (e.g. 18.x on some hosts) reject ``FORMAT Parquet`` with Code 73.
    """
    major = _client_major_version(client)
    return major is not None and major >= 22


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # S603: list-args built from operator CLI flags, explicit shell=False
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False, shell=False)  # noqa: S603


def load_with_clickhouse_client(dsn: ChDsn, dataset: str, output_dir: str) -> None:
    """Load via clickhouse-client, streaming each Parquet chunk (FORMAT Parquet).

    Password is passed through the CLICKHOUSE_PASSWORD env var -- never argv.
    """
    client = clickhouse_client_available()
    if client is None:
        raise FileNotFoundError("clickhouse-client not found")
    env = dict(os.environ)
    argv_password = False
    if dsn.password:
        if _client_honors_password_env(client):
            env["CLICKHOUSE_PASSWORD"] = dsn.password
        else:
            # Old clients ignore CLICKHOUSE_PASSWORD; password lands in argv
            # (briefly visible in ps) -- the emitted SQL file remains the
            # no-password-in-argv alternative.
            argv_password = True
    base = [
        client,
        "--host",
        dsn.host,
        "--port",
        str(dsn.port),
        "--user",
        dsn.user,
        "--database",
        dsn.database,
    ]
    if argv_password:
        base += ["--password", dsn.password]
    # Ensure the table exists before streaming.
    ddl = _run([*base, "--query", generate_create_table(dataset, dsn.database)], env=env)
    if ddl.returncode != 0:
        raise RuntimeError(f"CREATE TABLE failed: {ddl.stderr.strip()}")
    chunks = sorted(Path(output_dir).resolve().glob("*.parquet"))
    if not chunks:
        raise RuntimeError(f"no parquet files found in {output_dir}")
    for chunk in chunks:
        with chunk.open("rb") as fh:
            # S603: list-args built from trusted DSN/CLI flags, explicit shell=False
            res = subprocess.run(  # noqa: S603
                [*base, "--query", f"INSERT INTO `{dsn.database}`.`{dataset}` FORMAT Parquet"],
                stdin=fh,
                capture_output=True,
                text=True,
                env=env,
                check=False,
                shell=False,
            )
            if res.returncode != 0:
                raise RuntimeError(f"INSERT {chunk.name} failed: {res.stderr.strip()}")


def load_with_clickhouse_local(dsn: ChDsn, dataset: str, output_dir: str) -> None:
    """Load via clickhouse-local + the ``remote()`` table function.

    NOTE: the password appears inside the query string here because
    clickhouse-local's remote() has no env-var equivalent. For production,
    prefer the clickhouse-client path (or the emitted SQL file).
    """
    local = clickhouse_local_available()
    if local is None:
        raise FileNotFoundError("clickhouse-local not found")
    # remote('host:port', 'db.table', 'user', 'password') -- table must already exist.
    remote = f"remote('{dsn.host}:{dsn.port}', '{dsn.database}.{dataset}', '{dsn.user}', '{dsn.password}')"
    ddl = _run([local, "--query", generate_create_table(dataset, dsn.database)])
    if ddl.returncode != 0:
        raise RuntimeError(f"clickhouse-local CREATE TABLE failed: {ddl.stderr.strip()}")
    chunks = sorted(Path(output_dir).resolve().glob("*.parquet"))
    if not chunks:
        raise RuntimeError(f"no parquet files found in {output_dir}")
    for chunk in chunks:
        # S608: query is built from the operator-supplied DSN (trusted, local
        # CLI tool); components are quoted for the remote() function.
        query = f"INSERT INTO TABLE FUNCTION {remote} SELECT * FROM file('{chunk}', 'Parquet')"  # noqa: S608
        res = _run([local, "--query", query])
        if res.returncode != 0:
            raise RuntimeError(f"clickhouse-local INSERT {chunk.name} failed: {res.stderr.strip()}")


def load_parquet(dsn_str: str, dataset: str, output_dir: str) -> str | None:
    """Attempt a direct load. Returns a warning string on soft failure, None on success."""
    dsn = ChDsn.parse(dsn_str)
    client = clickhouse_client_available()
    if client is not None and _client_supports_parquet(client):
        load_with_clickhouse_client(dsn, dataset, output_dir)
        return None
    if client is not None:
        print(
            f"WARNING: clickhouse-client {_client_major_version(client)} lacks "
            "Parquet ingest (needs >= 22.x); trying clickhouse-local instead.",
            file=sys.stderr,
        )
    if clickhouse_local_available() is not None:
        load_with_clickhouse_local(dsn, dataset, output_dir)
        return None
    return CLICKHOUSE_CLIENT_HINT


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cryo_backfill",
        description=(
            "Extract EVM on-chain data with cryo (Parquet) and load it into ClickHouse. "
            "Binary columns are hex-encoded (--hex) so they map to ClickHouse String."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dataset",
        required=True,
        choices=sorted(DATASETS),
        help="cryo dataset to extract",
    )
    parser.add_argument("--start", type=int, required=True, help="first block (inclusive)")
    parser.add_argument("--end", type=int, required=True, help="last block (inclusive)")
    parser.add_argument("--rpc-url", default=None, help="EVM RPC URL (falls back to cryo's ETH_RPC_URL/MESC)")
    parser.add_argument(
        "--output-dir",
        default=".",
        help="directory for cryo Parquet output + generated SQL",
    )
    parser.add_argument(
        "--clickhouse-dsn",
        default=None,
        help="clickhouse://user:pass@host:port/db -- load directly (needs clickhouse-client or clickhouse-local)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="pass --overwrite to cryo (re-extract existing chunks)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the cryo command and exit without running it",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cmd = build_cryo_cmd(
        args.dataset,
        args.start,
        args.end,
        args.output_dir,
        rpc_url=args.rpc_url,
        overwrite=args.overwrite,
    )
    print(f"cryo command: {shlex.join(cmd)}")
    if args.dry_run:
        return 0

    if shutil.which("cryo") is None:
        print(CRYO_INSTALL_HINT, file=sys.stderr)
        return 2

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    res = _run(cmd)
    if res.returncode != 0:
        print(f"cryo failed (exit {res.returncode}):\n{res.stderr.strip()}", file=sys.stderr)
        return 1

    # cryo exits 0 even when every chunk errors (e.g. RPC auth/archive
    # failures) and writes zero rows. Verify actual Parquet output exists.
    parquet_chunks = sorted(Path(args.output_dir).resolve().glob("*.parquet"))
    if not parquet_chunks:
        print(
            "cryo produced no *.parquet files in output dir; check the RPC "
            "(archive access / auth) and cryo's .cryo/reports/. No SQL written.",
            file=sys.stderr,
        )
        return 1

    database = DEFAULT_CH_DB
    if args.clickhouse_dsn:
        try:
            database = ChDsn.parse(args.clickhouse_dsn).database
        except ValueError as exc:
            print(f"invalid --clickhouse-dsn: {exc}", file=sys.stderr)
            return 2

    sql_path = write_sql_file(args.dataset, database, args.output_dir)
    print(f"wrote SQL: {sql_path}")

    if args.clickhouse_dsn:
        try:
            warning = load_parquet(args.clickhouse_dsn, args.dataset, args.output_dir)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            print(f"ClickHouse load failed: {exc}", file=sys.stderr)
            print(f"SQL file is still available at: {sql_path}", file=sys.stderr)
            return 1
        if warning:
            print(f"WARNING: {warning}", file=sys.stderr)
        else:
            print(f"loaded `{database}`.`{args.dataset}` from {args.output_dir}/*.parquet")
    else:
        print(
            f"no --clickhouse-dsn given; run the SQL file to load "
            f"`{database}`.`{args.dataset}` (or pass --clickhouse-dsn)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
