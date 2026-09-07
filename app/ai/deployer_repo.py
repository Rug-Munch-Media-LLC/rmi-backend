"""Deployer Reputation Repository — persistent memory of every deployer.

Tracks every deployer seen across all scans in ClickHouse (``deployer_repo``
table) and builds a historical pattern database: launch counts, confirmed
rugs, funding sources, risk-score history. Scans feed the repo (data
flywheel) and read it back so repeat offenders are flagged instantly.

Fail-open everywhere: if ClickHouse is unavailable, every method degrades
to empty/neutral results and never raises into the scan pipeline.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", os.getenv("CH_HOST", "rmi-clickhouse"))
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "9000"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DB = os.getenv("DEPLOYER_REPO_CLICKHOUSE_DB", "deployer_repo")

TABLE = "deployer_repo"

SCHEMA_SQL = f"""
CREATE DATABASE IF NOT EXISTS {CLICKHOUSE_DB};

CREATE TABLE IF NOT EXISTS {CLICKHOUSE_DB}.{TABLE} (
    deployer_address  String,
    chain             LowCardinality(String),
    token_address     String,
    token_symbol      String DEFAULT '',
    launch_time       DateTime,
    funding_source    String DEFAULT '',
    initial_liquidity Float64 DEFAULT 0,
    risk_score        Float32 DEFAULT 0,
    outcome           LowCardinality(String) DEFAULT 'pending',
    updated_at        DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (deployer_address, chain, token_address)
"""

_VALID_OUTCOMES = {"rug", "clean", "pending"}


def _get_client() -> Any | None:
    """Lazy ClickHouse client. Returns None if unavailable (fail-open)."""
    try:
        import clickhouse_driver

        client = clickhouse_driver.Client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            user=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DB,
            connect_timeout=5,
            send_receive_timeout=30,
        )
        client.execute("SELECT 1")
        return client
    except Exception as e:
        log.warning("deployer_repo_clickhouse_unavailable err=%s", e)
        return None


async def ensure_schema() -> bool:
    """Create the deployer_repo database + table. Safe to call repeatedly."""
    client = await asyncio.to_thread(_get_client)
    if client is None:
        return False
    try:
        for stmt in SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                await asyncio.to_thread(client.execute, stmt)
        return True
    except Exception as e:
        log.warning("deployer_repo_schema_failed err=%s", e)
        return False


class DeployerRepo:
    """Tracks every deployer across all scans. Builds historical patterns."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    async def _ch(self) -> Any | None:
        if self._client is None:
            self._client = await asyncio.to_thread(_get_client)
        return self._client

    # ── Record ────────────────────────────────────────────────────────

    async def record_deployer(self, address: str, chain: str, token: str, scan_result: dict) -> bool:
        """Record a deployer from a scan result.

        Stores: deployer address, chain, token launched, launch time,
        funding source, initial liquidity, scan risk score, outcome.
        """
        addr = (address or "").strip().lower()
        if not addr or not token:
            return False
        client = await self._ch()
        if client is None:
            return False

        result = scan_result if isinstance(scan_result, dict) else {}
        try:
            risk = float(result.get("risk_score", 0) or 0)
        except (TypeError, ValueError):
            risk = 0.0
        try:
            liquidity = float(result.get("initial_liquidity", 0) or 0)
        except (TypeError, ValueError):
            liquidity = 0.0
        outcome = str(result.get("outcome") or "pending")

        row = (
            addr,
            chain or "",
            token,
            str(result.get("symbol") or ""),
            datetime.now(UTC).replace(tzinfo=None),
            str(result.get("funding_source") or ""),
            liquidity,
            risk,
            outcome if outcome in _VALID_OUTCOMES else "pending",
            datetime.now(UTC).replace(tzinfo=None),
        )
        try:
            await asyncio.to_thread(
                client.execute,
                "INSERT INTO "
                + TABLE
                + """
                (deployer_address, chain, token_address, token_symbol, launch_time,
                 funding_source, initial_liquidity, risk_score, outcome, updated_at)
                VALUES""",
                [row],
            )
            return True
        except Exception as e:
            log.warning("deployer_repo_record_failed deployer=%s err=%s", addr[:10], e)
            return False

    # ── History ───────────────────────────────────────────────────────

    async def get_deployer_history(self, address: str, chain: str) -> dict:
        """Full history of a deployer: launches, rugs, funding patterns, rug rate."""
        empty: dict[str, Any] = {
            "address": (address or "").lower(),
            "chain": chain,
            "total_launches": 0,
            "confirmed_rugs": 0,
            "confirmed_clean": 0,
            "pending": 0,
            "first_seen": None,
            "last_seen": None,
            "funding_patterns": [],
            "avg_risk_score": 0.0,
            "tokens": [],
            "rug_rate": 0.0,
        }
        client = await self._ch()
        if client is None:
            return empty
        try:
            rows = await asyncio.to_thread(
                client.execute,
                "SELECT token_address, token_symbol, launch_time, funding_source, "
                "initial_liquidity, risk_score, outcome "
                "FROM " + TABLE + " FINAL "
                "WHERE deployer_address = %(addr)s AND chain = %(chain)s "
                "ORDER BY launch_time DESC "
                "LIMIT 200",
                {"addr": (address or "").lower(), "chain": chain},
            )
        except Exception as e:
            log.warning("deployer_repo_history_failed deployer=%s err=%s", (address or "")[:10], e)
            return empty

        if not rows:
            return empty

        tokens = [
            {
                "token": r[0],
                "symbol": r[1],
                "timestamp": str(r[2]),
                "funding_source": r[3],
                "initial_liquidity": float(r[4]),
                "risk_score": float(r[5]),
                "outcome": r[6],
            }
            for r in rows
        ]
        outcomes = [t["outcome"] for t in tokens]
        confirmed_rugs = sum(1 for o in outcomes if o == "rug")
        confirmed_clean = sum(1 for o in outcomes if o == "clean")
        pending = sum(1 for o in outcomes if o == "pending")
        total = len(tokens)
        funding_patterns = sorted({t["funding_source"] for t in tokens if t["funding_source"]})

        return {
            "address": (address or "").lower(),
            "chain": chain,
            "total_launches": total,
            "confirmed_rugs": confirmed_rugs,
            "confirmed_clean": confirmed_clean,
            "pending": pending,
            "first_seen": tokens[-1]["timestamp"],
            "last_seen": tokens[0]["timestamp"],
            "funding_patterns": funding_patterns,
            "avg_risk_score": round(sum(t["risk_score"] for t in tokens) / total, 2),
            "tokens": tokens,
            "rug_rate": round(confirmed_rugs / total * 100, 1) if total else 0.0,
        }

    # ── Outcome updates ───────────────────────────────────────────────

    async def update_outcome(self, address: str, chain: str, token: str, outcome: str) -> bool:
        """Update the outcome of a previously tracked deployer token.

        outcome: 'rug' | 'clean' | 'pending'
        """
        outcome = (outcome or "").strip().lower()
        if outcome not in _VALID_OUTCOMES:
            log.warning("deployer_repo_invalid_outcome outcome=%s", outcome)
            return False
        client = await self._ch()
        if client is None:
            return False
        try:
            # Read the existing row so we preserve its fields, then re-insert
            # with the new outcome (ReplacingMergeTree keeps the latest).
            rows = await asyncio.to_thread(
                client.execute,
                "SELECT token_symbol, launch_time, funding_source, initial_liquidity, risk_score "
                "FROM " + TABLE + " FINAL "
                "WHERE deployer_address = %(addr)s AND chain = %(chain)s "
                "AND token_address = %(token)s "
                "LIMIT 1",
                {"addr": (address or "").lower(), "chain": chain, "token": token},
            )
            if not rows:
                log.warning("deployer_repo_outcome_no_row deployer=%s token=%s", (address or "")[:10], token[:10])
                return False
            symbol, launch_time, funding, liquidity, risk = rows[0]
            await asyncio.to_thread(
                client.execute,
                "INSERT INTO "
                + TABLE
                + """
                (deployer_address, chain, token_address, token_symbol, launch_time,
                 funding_source, initial_liquidity, risk_score, outcome, updated_at)
                VALUES""",
                [
                    (
                        (address or "").lower(),
                        chain,
                        token,
                        symbol,
                        launch_time,
                        funding,
                        float(liquidity),
                        float(risk),
                        outcome,
                        datetime.now(UTC).replace(tzinfo=None),
                    )
                ],
            )
            return True
        except Exception as e:
            log.warning("deployer_repo_outcome_update_failed err=%s", e)
            return False

    # ── Repeat offenders ──────────────────────────────────────────────

    async def find_repeat_deployers(self, chain: str, limit: int = 50) -> list[dict]:
        """Deployers with >1 launch on a chain, sorted by rug rate (desc)."""
        client = await self._ch()
        if client is None:
            return []
        try:
            rows = await asyncio.to_thread(
                client.execute,
                "SELECT deployer_address, "
                "count() AS total_launches, "
                "countIf(outcome = 'rug') AS confirmed_rugs, "
                "countIf(outcome = 'clean') AS confirmed_clean, "
                "avg(risk_score) AS avg_risk, "
                "min(launch_time) AS first_seen, "
                "max(launch_time) AS last_seen "
                "FROM " + TABLE + " FINAL "
                "WHERE chain = %(chain)s "
                "GROUP BY deployer_address "
                "HAVING total_launches > 1 "
                "ORDER BY confirmed_rugs DESC, avg_risk DESC "
                "LIMIT %(limit)s",
                {"chain": chain, "limit": limit},
            )
        except Exception as e:
            log.warning("deployer_repo_repeat_query_failed err=%s", e)
            return []
        return [
            {
                "deployer": r[0],
                "total_launches": int(r[1]),
                "confirmed_rugs": int(r[2]),
                "confirmed_clean": int(r[3]),
                "avg_risk_score": round(float(r[4]), 2),
                "rug_rate": round(int(r[2]) / int(r[1]) * 100, 1) if r[1] else 0.0,
                "first_seen": str(r[5]),
                "last_seen": str(r[6]),
            }
            for r in rows
        ]

    # ── Repo stats (admin endpoint) ───────────────────────────────────

    async def repo_stats(self) -> dict:
        """Totals: deployers tracked, confirmed rugs, repeat offenders."""
        client = await self._ch()
        if client is None:
            return {
                "total_deployers": 0,
                "total_launches_tracked": 0,
                "confirmed_rugs": 0,
                "confirmed_clean": 0,
                "pending_outcomes": 0,
                "repeat_offenders": 0,
                "available": False,
            }
        try:
            rows = await asyncio.to_thread(
                client.execute,
                "SELECT "
                "uniqExact(deployer_address) AS total_deployers, "
                "count() AS total_launches, "
                "countIf(outcome = 'rug') AS confirmed_rugs, "
                "countIf(outcome = 'clean') AS confirmed_clean, "
                "countIf(outcome = 'pending') AS pending_outcomes "
                "FROM " + TABLE + " FINAL",
            )
            repeat_rows = await asyncio.to_thread(
                client.execute,
                "SELECT count() FROM ("
                "SELECT deployer_address "
                "FROM " + TABLE + " FINAL "
                "GROUP BY deployer_address "
                "HAVING count() > 1 AND countIf(outcome = 'rug') > 0"
                ")",
            )
            r = rows[0] if rows else (0, 0, 0, 0, 0)
            return {
                "total_deployers": int(r[0]),
                "total_launches_tracked": int(r[1]),
                "confirmed_rugs": int(r[2]),
                "confirmed_clean": int(r[3]),
                "pending_outcomes": int(r[4]),
                "repeat_offenders": int(repeat_rows[0][0]) if repeat_rows else 0,
                "available": True,
            }
        except Exception as e:
            log.warning("deployer_repo_stats_failed err=%s", e)
            return {
                "total_deployers": 0,
                "total_launches_tracked": 0,
                "confirmed_rugs": 0,
                "confirmed_clean": 0,
                "pending_outcomes": 0,
                "repeat_offenders": 0,
                "available": False,
                "error": "stats_unavailable",
            }


# ── Global singleton ────────────────────────────────────────────────────────

_repo: DeployerRepo | None = None


def get_deployer_repo() -> DeployerRepo:
    """Get the global deployer repo instance."""
    global _repo
    if _repo is None:
        _repo = DeployerRepo()
    return _repo
