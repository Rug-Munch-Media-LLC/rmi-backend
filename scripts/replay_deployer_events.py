#!/usr/bin/env python3
"""
Replay deployer events into ClickHouse wallet_memory.deployer_events.

Reads from:
  1. Chain-indexer deployer state files (*.json) for tracked token addresses
  2. Backup NDJSON files (.zst.age) if available and decryptable

Writes to:
  ClickHouse wallet_memory.deployer_events table

Usage:
  python3 scripts/replay_deployer_events.py
  python3 scripts/replay_deployer_events.py --dry-run       # preview only
  python3 scripts/replay_deployer_events.py --insert-only   # skip state files, just insert from NDJSON

Environment (or .env):
  CLICKHOUSE_URL=http://rmi:password@localhost:8123
  CLICKHOUSE_USER=rmi
  CLICKHOUSE_PASSWORD=clickhouse_rmi_prod_2026
  CLICKHOUSE_DATABASE=wallet_memory
  STATE_DIR=/path/to/chain-indexer/state
  BACKUP_DIR=/path/to/backups/chain-indexer
"""

import json
import os
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CLICKHOUSE_URL = os.environ.get(
    "CLICKHOUSE_URL",
    "http://localhost:8123",
)
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.environ.get(
    "CLICKHOUSE_PASSWORD",
    "clickhouse_rmi_prod_2026",
)
CLICKHOUSE_DB = os.environ.get("CLICKHOUSE_DATABASE", "wallet_memory")
STATE_DIR = Path(os.environ.get("STATE_DIR", "/path/to/chain-indexer/state"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/path/to/backups/chain-indexer"))
TABLE = f"{CLICKHOUSE_DB}.deployer_events"

DRY_RUN = "--dry-run" in sys.argv
INSERT_ONLY = "--insert-only" in sys.argv

# ---------------------------------------------------------------------------
# ClickHouse helpers
# ---------------------------------------------------------------------------

try:
    import clickhouse_connect

    def get_ch_client():
        return clickhouse_connect.get_client(
            host=CLICKHOUSE_URL.replace("http://", "").replace("https://", "").split("@")[-1].split(":")[0]
            if "http" in CLICKHOUSE_URL
            else CLICKHOUSE_URL,
            port=int(
                CLICKHOUSE_URL.split(":")[-1].split("@")[-1].split("/")[0]
                if ":" in CLICKHOUSE_URL.replace("http://", "").replace("https://", "")
                else 8123
            ),
            username=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DB,
        )
except ImportError:
    import urllib.request

    def get_ch_client():
        """Return a dict-based client using HTTP basic auth."""
        # Parse URL parts
        url = CLICKHOUSE_URL
        user = CLICKHOUSE_USER
        pw = CLICKHOUSE_PASSWORD
        host = url
        if "http" in url:
            # Handle embedded credentials
            if "@" in url:
                auth_part = url.split("@")[0]
                if "//" in auth_part:
                    auth_part = auth_part.split("//")[1]
                if ":" in auth_part:
                    parts = auth_part.split(":")
                    user = parts[0]
                    pw = ":".join(parts[1:])
                host = url.split("@")[1]
            else:
                host = url.replace("http://", "").replace("https://", "")
        return {"host": host, "user": user, "password": pw}

    class ClickHouseClient:
        def __init__(self, cfg):
            self.cfg = cfg

        def query(self, sql, params=None):
            import base64

            full_url = f"http://{self.cfg['host']}/?query={urllib.parse.quote(sql)}&default_format=JSON"
            auth = base64.b64encode(f"{self.cfg['user']}:{self.cfg['password']}".encode()).decode()
            req = urllib.request.Request(full_url)
            req.add_header("Authorization", f"Basic {auth}")
            try:
                resp = urllib.request.urlopen(req, timeout=30)
                data = json.loads(resp.read().decode())
                return data.get("data", [])
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                raise RuntimeError(f"ClickHouse query failed ({e.code}): {body[:500]}") from e

        def insert(self, table, columns, values):
            """Insert rows using JSONEachRow format."""
            import base64

            host = self.cfg["host"]
            rows_json = "\n".join(json.dumps(dict(zip(columns, row, strict=False))) for row in values)
            full_url = f"http://{host}/?query=INSERT+INTO+{urllib.parse.quote(table)}+FORMAT+JSONEachRow"
            auth = base64.b64encode(f"{self.cfg['user']}:{self.cfg['password']}".encode()).decode()
            req = urllib.request.Request(
                full_url,
                data=rows_json.encode("utf-8"),
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/x-ndjson",
                },
            )
            try:
                resp = urllib.request.urlopen(req, timeout=60)
                return resp.status == 200
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                raise RuntimeError(f"ClickHouse insert failed ({e.code}): {body[:500]}") from e

        def close(self):
            pass

# ---------------------------------------------------------------------------
# Parse state files
# ---------------------------------------------------------------------------


def parse_tracked_tokens(state_dir: Path):
    """Parse deployer state files and yield tracked tokens with metadata."""
    state_files = sorted(state_dir.glob("deployers_*.json"))
    if not state_files:
        print(f"  [WARN] No state files found in {state_dir}")
        return

    for sf in state_files:
        chain = sf.stem.replace("deployers_", "")
        try:
            data = json.loads(sf.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [ERROR] Failed to parse {sf.name}: {e}")
            continue

        tokens = data.get("trackedTokens", [])
        last_block = data.get("lastBlock", 0)
        print(f"  {chain}: {len(tokens)} tracked tokens (last_block={last_block})")

        now_ts = datetime.now(UTC)
        epoch_ts = datetime(1970, 1, 1, tzinfo=UTC)
        for addr in tokens:
            yield {
                "deployer": "",
                "token_address": addr.lower() if addr.startswith("0x") else addr,
                "chain": chain,
                "block_number": 0,
                "block_timestamp": epoch_ts,
                "tx_hash": "",
                "type": "token",
                "factory": None,
                "ingested_at": now_ts,
            }


# ---------------------------------------------------------------------------
# Parse backup NDJSON files
# ---------------------------------------------------------------------------


def find_backup_files(backup_dir: Path):
    """Find deployer NDJSON files (plain, .zst, .zst.age) in backup dir."""
    found = []

    # Plain .jsonl
    found.extend(backup_dir.rglob("**/deployers/**/deployments.jsonl"))

    # Compressed .jsonl.zst
    found.extend(backup_dir.rglob("**/deployers/**/deployments.jsonl.zst"))

    # Encrypted + compressed .jsonl.zst.age
    found.extend(backup_dir.rglob("**/deployers/**/deployments.jsonl.zst.age"))

    return sorted(found)


def read_backup_events(filepath: Path):
    """Read deployer events from a backup file, decompressing if needed."""
    suffix = filepath.suffix

    if suffix == ".age":
        # Age-encrypted + zstd compressed
        print(f"    [SKIP] Age-encrypted file: {filepath} (need age key to decrypt)")
        return []

    if suffix == ".zst":
        # Zstd compressed
        import zstandard

        try:
            with open(filepath, "rb") as f:
                dctx = zstandard.ZstdDecompressor()
                data = dctx.decompress(f.read())
            return parse_ndjson_lines(data.decode("utf-8"))
        except Exception as e:
            print(f"    [ERROR] Failed to decompress {filepath}: {e}")
            return []

    if suffix == ".jsonl" or filepath.suffix == ".jsonl":
        try:
            return parse_ndjson_lines(filepath.read_text())
        except Exception as e:
            print(f"    [ERROR] Failed to read {filepath}: {e}")
            return []

    return []


def _parse_ts(val):
    """Convert a timestamp value to a datetime object."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=UTC)
    if isinstance(val, str):
        # Try ISO format
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    return datetime(1970, 1, 1, tzinfo=UTC)


def parse_ndjson_lines(text: str):
    """Parse NDJSON lines into deployment event dicts."""
    events = []
    now_ts = datetime.now(UTC)
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue

        events.append(
            {
                "deployer": ev.get("deployer", ""),
                "token_address": ev.get("tokenAddress", ev.get("token_address", "")),
                "chain": ev.get("chain", ""),
                "block_number": ev.get("blockNumber", ev.get("block_number", 0)),
                "block_timestamp": _parse_ts(ev.get("blockTimestamp", ev.get("block_timestamp", ""))),
                "tx_hash": ev.get("txHash", ev.get("tx_hash", "")),
                "type": ev.get("type", "token"),
                "factory": ev.get("factory", None),
                "ingested_at": now_ts,
            }
        )
    return events


# ---------------------------------------------------------------------------
# ClickHouse operations
# ---------------------------------------------------------------------------


def ensure_table(ch):
    """Ensure the deployer_events table exists."""
    ddl = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        deployer       String,
        token_address  String,
        chain          String,
        block_number   UInt64,
        block_timestamp DateTime,
        tx_hash        String,
        type           String,
        factory        Nullable(String),
        ingested_at    DateTime
    ) ENGINE = MergeTree
    ORDER BY (chain, block_number)
    TTL ingested_at + INTERVAL 90 DAY"""
    try:
        ch.query(ddl)
        print(f"  Table {TABLE} OK")
    except Exception as e:
        print(f"  Table {TABLE}: {e}")


def insert_batch(ch, rows, batch_size=5000):
    """Insert rows into ClickHouse in batches."""
    if not rows:
        return 0

    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        columns = [
            "deployer",
            "token_address",
            "chain",
            "block_number",
            "block_timestamp",
            "tx_hash",
            "type",
            "factory",
            "ingested_at",
        ]
        values = [
            (
                r["deployer"],
                r["token_address"],
                r["chain"],
                r["block_number"],
                r["block_timestamp"],
                r["tx_hash"],
                r["type"],
                r["factory"],
                r["ingested_at"],
            )
            for r in batch
        ]
        if DRY_RUN:
            print(f"    [DRY-RUN] Would insert {len(batch)} rows")
            total += len(batch)
            continue

        try:
            ch.insert(TABLE, columns, values)
            total += len(batch)
            print(f"    Inserted {len(batch)} rows (total: {total})")
        except Exception as e:
            print(f"    [ERROR] Batch insert failed at row {i}: {e}")

    return total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * 60)
    print(f"Deployer Events Replay — {datetime.now(UTC).isoformat()}")
    print(f"  Table:    {TABLE}")
    print(f"  State:    {STATE_DIR}")
    print(f"  Backups:  {BACKUP_DIR}")
    if DRY_RUN:
        print("  MODE:     DRY RUN (no writes)")
    print("=" * 60)

    # Connect to ClickHouse
    try:
        config = get_ch_client()
        if isinstance(config, dict):
            ch = ClickHouseClient(config)
        else:
            # clickhouse_connect client — wrap with compatible interface
            class CHWrapper:
                def __init__(self, client):
                    self._client = client

                def query(self, sql, params=None):
                    return (
                        self._client.query(sql, parameters=params).result_rows
                        if params
                        else self._client.query(sql).result_rows
                    )

                def insert(self, table, columns, values):
                    self._client.insert(table, values, column_names=columns)

                def close(self):
                    self._client.close()

            ch = CHWrapper(config)
    except Exception as e:
        print(f"\n[FATAL] Cannot connect to ClickHouse: {e}")
        sys.exit(1)

    # Ensure table exists
    print("\n[1/3] Ensuring table exists...")
    ensure_table(ch)

    # Collect all rows
    all_rows = []

    # Source 1: State files (tracked tokens)
    if not INSERT_ONLY:
        print("\n[2/3] Reading state files...")
        for row in parse_tracked_tokens(STATE_DIR):
            all_rows.append(row)

    # Source 2: Backup NDJSON files
    print("\n[2/3] Finding backup deployer files...")
    backup_files = find_backup_files(BACKUP_DIR)
    if backup_files:
        for bf in backup_files:
            print(f"  Found: {bf.relative_to(BACKUP_DIR) if bf.is_relative_to(BACKUP_DIR) else bf}")
            events = read_backup_events(bf)
            print(f"    -> {len(events)} events")
            all_rows.extend(events)
    else:
        print("  No backup deployer files found")

    # Deduplicate by token_address + chain + type
    print(f"\n  Total raw rows: {len(all_rows)}")
    seen = set()
    deduped = []
    for row in all_rows:
        key = (row["token_address"], row["chain"], row["type"])
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    print(f"  After dedup:   {len(deduped)} rows")

    # Insert
    print("\n[3/3] Inserting into ClickHouse...")
    if not deduped:
        print("  No rows to insert.")
        ch.close()
        return

    total = insert_batch(ch, deduped)
    ch.close()

    print("\n" + "=" * 60)
    if DRY_RUN:
        print(f"DRY RUN complete — would have inserted {total} rows")
    else:
        print(f"SUCCESS — inserted {total} rows into {TABLE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
