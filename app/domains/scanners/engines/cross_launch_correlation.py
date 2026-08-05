"""
Cross-Launch Wallet Correlation — Repeat Bundler Detection
============================================================
Tracks wallets that appear as early buyers across multiple token launches.
A wallet that shows up as an early buyer in 5+ different token launches is
almost certainly part of a bundler ring — one of the strongest signals for
coordinated activity.

Wallet scoring (cumulative bands, capped at 100):
    - 3+  launches: suspicious  (+20)
    - 5+  launches: high        (+35)
    - 10+ launches: critical    (+50)
    - Single-chain pattern      (+10)

Storage:
    - Primary: lightweight JSON store at ``/srv/data/wallet_correlation.json``
      (override with ``WALLET_CORRELATION_PATH``). Writes are atomic
      (temp file + ``os.replace``) and bounded at ``MAX_STORE_WALLETS``.
    - The existing ``scan_history`` SQLite table only records
      ``(user_id, token_address, chain, scan_type, risk_score)`` — it carries
      no wallet-level buyer data, so it cannot back this feature.
    - ClickHouse ``wallet_memory`` (deployer_events + evm_transfers) is used
      best-effort by :func:`hydrate_from_clickhouse` to seed the store with
      early-buyer observations from recent launches. Every query is bounded
      and degrades to a no-op when ClickHouse is unavailable.

Usage:
    from app.domains.scanners.engines.cross_launch_correlation import analyze_cross_launch

    flags = await analyze_cross_launch(
        early_buyers=buyers, chain="solana", deploy_block=123, token_address="...",
    )
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.core.logging import get_logger
from app.domains.databus.providers.chains.chain_indexer import _get_ch_client
from app.domains.scanner.engines._common import run_sync

logger = get_logger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────
STORE_PATH = os.getenv("WALLET_CORRELATION_PATH", "/srv/data/wallet_correlation.json")
STORE_VERSION = 1

EARLY_BUY_BLOCK_WINDOW = 10  # matches bundle_detector "first 10 blocks"
MAX_STORE_WALLETS = 50_000
MAX_WALLETS_PER_LAUNCH = 100

LAUNCH_THRESHOLD_SUSPICIOUS = 3
LAUNCH_THRESHOLD_HIGH = 5
LAUNCH_THRESHOLD_CRITICAL = 10

SCORE_SUSPICIOUS = 20  # 3+ launches
SCORE_HIGH = 35  # 5+ launches
SCORE_CRITICAL = 50  # 10+ launches
SCORE_SAME_CHAIN_BONUS = 10
MAX_SCORE = 100

HYDRATE_RECENT_DAYS = 30
HYDRATE_MAX_TOKENS = 50
HYDRATE_MAX_BUYERS_PER_TOKEN = 20

# Addresses that can never be a buyer wallet.
_NON_WALLET_ADDRESSES = frozenset(
    {
        "",
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead",
    }
)


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class WalletLaunchRecord:
    """One early-buy observation of a wallet in a single token launch."""

    token_address: str
    chain: str
    buy_block_offset: int
    timestamp: float  # unix seconds


@dataclass
class WalletStats:
    """Aggregated cross-launch stats for a wallet."""

    wallet_address: str
    token_launches_count: int
    first_seen: float
    last_seen: float
    avg_buy_block_offset: float
    chains: list[str]
    score: int = 0
    level: str = "NONE"  # NONE, SUSPICIOUS, HIGH, CRITICAL
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_address": self.wallet_address,
            "token_launches_count": self.token_launches_count,
            "first_seen": _iso(self.first_seen),
            "last_seen": _iso(self.last_seen),
            "avg_buy_block_offset": round(self.avg_buy_block_offset, 2),
            "chains": self.chains,
            "score": self.score,
            "level": self.level,
            "flags": self.flags,
        }


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _normalize_wallet(address: str) -> str:
    """Normalize a wallet address for correlation keys.

    EVM addresses are case-insensitive (lowercased); Solana addresses are
    case-sensitive base58 and are left untouched.
    """
    addr = (address or "").strip()
    if addr.startswith(("0x", "0X")):
        return addr.lower()
    return addr


def _buyer_address(buyer: Any) -> str:
    """Extract a wallet address from an early-buyer object or dict."""
    if isinstance(buyer, dict):
        return str(buyer.get("address", "") or "")
    return str(getattr(buyer, "address", "") or "")


def _buyer_block(buyer: Any) -> int:
    """Extract the buy block from an early-buyer object or dict."""
    if isinstance(buyer, dict):
        return int(buyer.get("first_buy_block") or buyer.get("buy_block") or 0)
    return int(getattr(buyer, "first_buy_block", 0) or getattr(buyer, "buy_block", 0) or 0)


def _is_plausible_wallet(address: str) -> bool:
    """True when an address is a plausible wallet (not zero/dead/empty)."""
    return address not in _NON_WALLET_ADDRESSES and len(address) >= 20


# ═══════════════════════════════════════════════════════════════════════════
# CORRELATION STORE
# ═══════════════════════════════════════════════════════════════════════════


class CrossLaunchCorrelator:
    """Correlation store for repeat early-buyer wallets (JSON-backed).

    The store is a ``{wallet_address: [WalletLaunchRecord, ...]}`` map kept in
    memory and persisted atomically to ``store_path``. This is a lightweight,
    bounded store by design — not a transactional DB. Concurrent writers may
    lose updates (last atomic replace wins) but the file is never corrupted.
    """

    def __init__(self, store_path: str | Path | None = None) -> None:
        self._store_path = Path(store_path or STORE_PATH)
        self._wallets: dict[str, list[WalletLaunchRecord]] = {}
        self._loaded = False

    # ── persistence ──────────────────────────────────────────────────

    def load(self) -> None:
        """Load the JSON store (idempotent; missing/corrupt file → empty store)."""
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = self._store_path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("wallet_correlation_store_corrupt path=%s", self._store_path)
            return
        wallets = data.get("wallets", {}) if isinstance(data, dict) else {}
        for address, records in wallets.items():
            parsed: list[WalletLaunchRecord] = []
            for rec in records or []:
                try:
                    parsed.append(
                        WalletLaunchRecord(
                            token_address=str(rec["token_address"]),
                            chain=str(rec["chain"]),
                            buy_block_offset=int(rec["buy_block_offset"]),
                            timestamp=float(rec["timestamp"]),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            if parsed:
                self._wallets[str(address)] = parsed

    def persist(self) -> None:
        """Atomically write the store (temp file + ``os.replace``)."""
        self._prune()
        payload: dict[str, Any] = {
            "version": STORE_VERSION,
            "updated_at": time.time(),
            "wallets": {
                address: [
                    {
                        "token_address": r.token_address,
                        "chain": r.chain,
                        "buy_block_offset": r.buy_block_offset,
                        "timestamp": r.timestamp,
                    }
                    for r in records
                ]
                for address, records in self._wallets.items()
            },
        }
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._store_path.parent), prefix=".wallet_correlation.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self._store_path)
        except OSError as exc:
            logger.warning("wallet_correlation_persist_failed: %s", exc)
            with contextlib.suppress(OSError):
                os.unlink(tmp)

    def _prune(self) -> None:
        """Bound the store: drop wallets with the fewest launches, then oldest."""
        if len(self._wallets) <= MAX_STORE_WALLETS:
            return
        over = len(self._wallets) - MAX_STORE_WALLETS
        ordered = sorted(
            self._wallets.items(),
            key=lambda item: (len(item[1]), max(r.timestamp for r in item[1])),
        )
        for address, _ in ordered[:over]:
            self._wallets.pop(address, None)

    # ── recording ────────────────────────────────────────────────────

    def record_launch(
        self,
        chain: str,
        token_address: str,
        deploy_block: int,
        early_buyers: list[Any],
        timestamp: float | None = None,
        persist: bool = True,
    ) -> int:
        """Record early buyers of one launch. Returns the number recorded.

        Buyers are deduped per ``(token_address, chain)`` so re-scans of the
        same token never double-count a launch. Only buyers within
        ``EARLY_BUY_BLOCK_WINDOW`` blocks of the deploy block are recorded.
        Recording requires a known ``deploy_block`` (> 0).
        """
        if deploy_block <= 0 or not early_buyers:
            return 0
        now = timestamp or time.time()
        token = (token_address or "").lower()
        recorded = 0
        for buyer in early_buyers[:MAX_WALLETS_PER_LAUNCH]:
            address = _normalize_wallet(_buyer_address(buyer))
            if not _is_plausible_wallet(address):
                continue
            offset = _buyer_block(buyer) - deploy_block
            if offset < 0 or offset > EARLY_BUY_BLOCK_WINDOW:
                continue
            records = self._wallets.setdefault(address, [])
            if any(r.token_address == token and r.chain == chain for r in records):
                continue  # same launch already recorded
            records.append(
                WalletLaunchRecord(
                    token_address=token,
                    chain=chain,
                    buy_block_offset=offset,
                    timestamp=now,
                )
            )
            recorded += 1
        if recorded and persist:
            self.persist()
        return recorded

    # ── aggregation ──────────────────────────────────────────────────

    def get_wallet_stats(self, wallet_address: str) -> WalletStats | None:
        """Return aggregated cross-launch stats for a wallet (or ``None``)."""
        key = _normalize_wallet(wallet_address)
        records = self._wallets.get(key)
        if not records:
            return None
        return self._aggregate(key, records)

    def all_wallet_stats(self, min_launches: int = 1) -> list[WalletStats]:
        """Return stats for every tracked wallet with >= ``min_launches``."""
        return [
            self._aggregate(address, records)
            for address, records in self._wallets.items()
            if len(records) >= min_launches
        ]

    def top_repeat_wallets(self, limit: int = 20, min_launches: int = 3) -> list[WalletStats]:
        """Return the highest-scoring repeat wallets (bundler-ring suspects)."""
        stats = self.all_wallet_stats(min_launches=min_launches)
        return sorted(stats, key=lambda s: (s.score, s.token_launches_count), reverse=True)[:limit]

    @staticmethod
    def _aggregate(address: str, records: list[WalletLaunchRecord]) -> WalletStats:
        unique_tokens = {(r.token_address, r.chain) for r in records}
        offsets = [r.buy_block_offset for r in records]
        chains = sorted({r.chain for r in records})
        stats = WalletStats(
            wallet_address=address,
            token_launches_count=len(unique_tokens),
            first_seen=min(r.timestamp for r in records),
            last_seen=max(r.timestamp for r in records),
            avg_buy_block_offset=sum(offsets) / len(offsets) if offsets else 0.0,
            chains=chains,
        )
        stats.score, stats.level, stats.flags = CrossLaunchCorrelator.score_wallet(stats)
        return stats

    # ── scoring ──────────────────────────────────────────────────────

    @staticmethod
    def score_wallet(stats: WalletStats) -> tuple[int, str, list[str]]:
        """Score a wallet's cross-launch stats (cumulative bands, capped 100).

        Bands stack (a 5+-launch wallet also satisfies 3+) per the spec's
        ``score += N`` wording; the result is capped at 100. The level is the
        highest band reached; the same-chain bonus never changes the level.
        """
        launches = stats.token_launches_count
        score = 0
        flags: list[str] = []
        level = "NONE"

        # Cumulative bands per spec ("score += N" for each band reached):
        # 3+ adds 20, 5+ adds 35, 10+ adds 50 — a 10-launch wallet reaches 105
        # and is capped at 100. The level reflects the highest band reached.
        if launches >= LAUNCH_THRESHOLD_SUSPICIOUS:
            score += SCORE_SUSPICIOUS
            level = "SUSPICIOUS"
        if launches >= LAUNCH_THRESHOLD_HIGH:
            score += SCORE_HIGH
            level = "HIGH"
        if launches >= LAUNCH_THRESHOLD_CRITICAL:
            score += SCORE_CRITICAL
            level = "CRITICAL"

        if launches >= LAUNCH_THRESHOLD_CRITICAL:
            flags.append("CROSS_LAUNCH_REPEAT_10PLUS")
        elif launches >= LAUNCH_THRESHOLD_HIGH:
            flags.append("CROSS_LAUNCH_REPEAT_5PLUS")
        elif launches >= LAUNCH_THRESHOLD_SUSPICIOUS:
            flags.append("CROSS_LAUNCH_REPEAT_3PLUS")

        # Same-chain bonus only sharpens wallets already flagged as repeaters.
        if launches >= LAUNCH_THRESHOLD_SUSPICIOUS and len(stats.chains) == 1:
            score += SCORE_SAME_CHAIN_BONUS
            flags.append("CROSS_LAUNCH_SAME_CHAIN")

        return min(score, MAX_SCORE), level, flags

    # ── cross-reference ──────────────────────────────────────────────

    def cross_reference(self, chain: str, early_buyers: list[Any]) -> dict[str, Any]:
        """Cross-reference a new token's early buyers against the store.

        Returns the ``cross_launch_flags`` payload for a BundleReport::

            {
                "result": "flagged" | "clean",
                "source": "json_store",
                "matched_wallets": [WalletStats.to_dict(), ...],
                "repeat_count": int,
                "max_wallet_score": int,
                "max_wallet_launches": int,
                "same_chain_pattern": bool,
                "score": int,   # token-level cross-launch score 0-100
                "flags": [str],
            }
        """
        self.load()
        matched: list[WalletStats] = []
        for buyer in early_buyers:
            address = _normalize_wallet(_buyer_address(buyer))
            if not _is_plausible_wallet(address):
                continue
            stats = self.get_wallet_stats(address)
            if stats and stats.token_launches_count >= LAUNCH_THRESHOLD_SUSPICIOUS:
                matched.append(stats)

        repeat_count = len(matched)
        max_score = max((s.score for s in matched), default=0)
        max_launches = max((s.token_launches_count for s in matched), default=0)
        same_chain_pattern = bool(matched) and all(set(s.chains) == {chain} for s in matched)

        token_score = 0
        if matched:
            # Strongest wallet signal, plus +5 per additional repeat wallet.
            token_score = max_score + 5 * (repeat_count - 1)

        flags: list[str] = []
        if matched:
            flags.append("CROSS_LAUNCH_REPEAT")
        if same_chain_pattern:
            flags.append("CROSS_LAUNCH_SAME_CHAIN_PATTERN")

        return {
            "result": "flagged" if matched else "clean",
            "source": "json_store",
            "matched_wallets": [s.to_dict() for s in matched[:20]],
            "repeat_count": repeat_count,
            "max_wallet_score": max_score,
            "max_wallet_launches": max_launches,
            "same_chain_pattern": same_chain_pattern,
            "score": min(token_score, MAX_SCORE),
            "flags": flags,
        }

    # ── monitoring / tests ───────────────────────────────────────────

    @property
    def store_path(self) -> Path:
        return self._store_path

    def clear(self) -> None:
        """Drop all in-memory records (does not touch disk)."""
        self._wallets = {}
        self._loaded = True

    def __len__(self) -> int:
        return len(self._wallets)

    # ── ClickHouse hydration ─────────────────────────────────────────

    def hydrate_from_clickhouse_sync(
        self,
        recent_days: int = HYDRATE_RECENT_DAYS,
        max_tokens: int = HYDRATE_MAX_TOKENS,
        max_buyers_per_token: int = HYDRATE_MAX_BUYERS_PER_TOKEN,
    ) -> dict[str, Any]:
        """Seed the store with early buyers of recent launches (best-effort).

        Pulls the most recent ``max_tokens`` launches from
        ``wallet_memory.deployer_events``, then for each launch collects the
        wallets receiving the token within the first 10 blocks (a buy transfer
        moves tokens pool → wallet, so recipients ≈ early buyers) and records
        them. Bounded queries; no-ops cleanly when ClickHouse is unavailable.
        """
        client = _get_ch_client()
        if client is None:
            return {
                "result": "unavailable",
                "reason": "clickhouse_unavailable",
                "launches_hydrated": 0,
                "wallets_recorded": 0,
            }

        cutoff = time.time() - recent_days * 86400
        try:
            launches = client.execute(
                """
                SELECT token_address, chain, block_number
                FROM wallet_memory.deployer_events
                WHERE block_timestamp >= toDateTime64(%(cutoff)s, 3)
                ORDER BY block_timestamp DESC
                LIMIT %(limit)s
                """,
                {"cutoff": cutoff, "limit": max_tokens},
                settings={"max_execution_time": 10},
            )
        except (ConnectionError, TimeoutError, RuntimeError, OSError, ValueError, CHDriverError) as exc:
            logger.debug("cross_launch_hydrate_launches_failed: %s", exc)
            return {
                "result": "error",
                "reason": str(exc)[:200],
                "launches_hydrated": 0,
                "wallets_recorded": 0,
            }

        hydrated = 0
        recorded = 0
        for row in (launches or [])[:max_tokens]:
            try:
                token, chain, deploy_block = str(row[0]), str(row[1]), int(row[2])
            except (IndexError, TypeError, ValueError):
                continue
            buyers = self._query_early_buyers(client, token, chain, deploy_block, max_buyers_per_token)
            if not buyers:
                continue
            recorded += self.record_launch(
                chain,
                token,
                deploy_block,
                [{"address": addr, "first_buy_block": deploy_block + offset} for addr, offset in buyers],
                timestamp=time.time(),
                persist=False,
            )
            hydrated += 1

        if recorded:
            self.persist()

        logger.info(
            "cross_launch_hydrate_done",
            launches=hydrated,
            wallets_recorded=recorded,
            days=recent_days,
        )
        return {"result": "ok", "launches_hydrated": hydrated, "wallets_recorded": recorded}

    @staticmethod
    def _query_early_buyers(
        client: Any,
        token: str,
        chain: str,
        deploy_block: int,
        limit: int,
    ) -> list[tuple[str, int]]:
        """Return ``[(buyer_address, block_offset)]`` for a launch's first blocks."""
        try:
            rows = client.execute(
                """
                SELECT to_address, block_number
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND block_number >= %(deploy_block)s
                  AND block_number <= %(deploy_block)s + 10
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                         '0x000000000000000000000000000000000000dead')
                ORDER BY block_number ASC
                LIMIT %(limit)s
                """,
                {"token": token, "chain": chain, "deploy_block": deploy_block, "limit": limit},
                settings={"max_execution_time": 10},
            )
        except (ConnectionError, TimeoutError, RuntimeError, OSError, ValueError, CHDriverError) as exc:
            logger.debug("cross_launch_early_buyers_failed: %s", exc)
            return []

        result: list[tuple[str, int]] = []
        for row in rows or []:
            try:
                result.append((str(row[0]), int(row[1]) - deploy_block))
            except (IndexError, TypeError, ValueError):
                continue
        return result


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

_correlator: CrossLaunchCorrelator | None = None


def get_correlator() -> CrossLaunchCorrelator:
    """Return the process-wide correlation store (lazy-loaded)."""
    global _correlator
    if _correlator is None:
        correlator = CrossLaunchCorrelator()
        correlator.load()
        _correlator = correlator
    return _correlator


def clear_correlator_cache() -> None:
    """Reset the singleton (for tests)."""
    global _correlator
    _correlator = None


async def analyze_cross_launch(
    early_buyers: list[Any],
    chain: str,
    deploy_block: int = 0,
    token_address: str = "",
    record: bool = True,
) -> dict[str, Any]:
    """Full cross-launch pass for a token scan: record + cross-reference.

    This is the entry point used by ``bundle_detector.detect_bundles()``.
    Never raises — failures degrade to an ``error`` result payload.
    """
    correlator = get_correlator()
    recorded = 0
    if record and token_address and deploy_block:
        try:
            recorded = await run_sync(correlator.record_launch, chain, token_address, deploy_block, early_buyers)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.debug("cross_launch_record_failed: %s", exc)
    try:
        flags: dict[str, Any] = await run_sync(correlator.cross_reference, chain, early_buyers)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.debug("cross_launch_reference_failed: %s", exc)
        return {"result": "error", "error": str(exc)[:200]}
    flags["recorded_wallets"] = recorded
    return flags


async def compute_cross_launch_score(early_buyers: list[Any], chain: str) -> float:
    """0-100 cross-launch score for a token's early buyers (composite input).

    Used by the bundler_detect composite via ``BundlerReport.cross_launch_score``.
    """
    flags = await analyze_cross_launch(early_buyers, chain, record=False)
    return float(flags.get("score", 0))


async def hydrate_from_clickhouse(
    recent_days: int = HYDRATE_RECENT_DAYS,
    max_tokens: int = HYDRATE_MAX_TOKENS,
    max_buyers_per_token: int = HYDRATE_MAX_BUYERS_PER_TOKEN,
) -> dict[str, Any]:
    """Seed the store from ClickHouse recent-launch early buyers (best-effort).

    Safe to call from a scheduler/cron; never blocks scan requests.
    """
    correlator = get_correlator()
    result: dict[str, Any] = await run_sync(
        correlator.hydrate_from_clickhouse_sync, recent_days, max_tokens, max_buyers_per_token
    )
    return result


def get_correlator_stats() -> dict[str, Any]:
    """Monitoring stats for the correlation store."""
    correlator = get_correlator()
    return {
        "store_path": str(correlator.store_path),
        "wallets_tracked": len(correlator),
        "wallets_3plus": sum(1 for _ in correlator.all_wallet_stats(LAUNCH_THRESHOLD_SUSPICIOUS)),
        "wallets_5plus": sum(1 for _ in correlator.all_wallet_stats(LAUNCH_THRESHOLD_HIGH)),
        "wallets_10plus": sum(1 for _ in correlator.all_wallet_stats(LAUNCH_THRESHOLD_CRITICAL)),
        "clickhouse_connected": _get_ch_client() is not None,
    }
