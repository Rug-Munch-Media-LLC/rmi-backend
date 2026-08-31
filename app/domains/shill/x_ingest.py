"""X (Twitter) shill-campaign ingest pipeline — KOL tweets → evidence stores.

Scheduled (see ``app/lifespan.py`` section 12) pull of recent tweets from
tracked KOL handles via the existing ApiPass X probe
(``app.domains.shill.apipass_x``), shill-signal extraction ($TICKER cashtags
+ token addresses), and persistence into the canonical evidence stores:

  * Qdrant ``threat_intel_v2`` (via ``evidence.write_evidence`` — the
    canonical write), and
  * the ``shill_evidence`` SQLite table (opportunistic mirror inside
    ``write_evidence``).

Idempotency: every ingested tweet ID is recorded in a small backend-owned
SQLite dedup ledger, so re-running the task never double-writes evidence.
Dedup degrades open when the ledger is unavailable (evidence writes are
idempotent-ish by content digest anyway).

Fail-open everywhere: no ApiPass key, rate-limited budget, dead embedder or
Qdrant outage degrades to a partial/empty summary — never raises.
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from typing import Any

from app.core.logging import get_logger
from app.domains.shill import apipass_x
from app.domains.shill.evidence import write_evidence

logger = get_logger("shill_x_ingest")

#: Blue-chip tickers are noise for shill detection (same set as detect.py).
_BLUECHIP = {"BTC", "ETH", "SOL", "USDT", "USDC", "BNB"}

#: Solana base58 mint / EVM 0x-address token identity (same shape as detect.py).
_TOKEN_ADDRESS_RE = re.compile(r"\b(?:0x[a-fA-F0-9]{6,64}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{2,10})\b")

#: How many top-scored KOL handles to poll per run (budget-bounded).
MAX_HANDLES_PER_RUN = int(os.getenv("RMI_X_SHILL_MAX_HANDLES", "5"))
#: Tweets pulled per handle per run.
TWEETS_PER_HANDLE = int(os.getenv("RMI_X_SHILL_TWEETS_PER_HANDLE", "10"))

#: Backend-owned dedup ledger (tweet_id PRIMARY KEY). Distinct from the bot's
#: ``shill_evidence`` DB — this one is ours to write, always.
_DEDUP_DB_PATH = os.getenv(
    "RMI_X_SHILL_DEDUP_DB",
    os.path.join(
        os.getenv("RMI_APIPASS_CACHE_DIR", os.path.join(tempfile.gettempdir(), "rmi", "apipass_cache")),
        "x_shill_seen.db",
    ),
)
#: Cap on ledger rows (bounded growth; oldest rows pruned on write).
_DEDUP_MAX_ROWS = int(os.getenv("RMI_X_SHILL_DEDUP_MAX_ROWS", "50000"))


# ══════════════════════════════════════════════════════════════════════════
# SIGNAL EXTRACTION — pure, deterministic
# ══════════════════════════════════════════════════════════════════════════


def extract_token_signals(text: str) -> list[str]:
    """Token identities mentioned in a tweet: non-bluechip $TICKERs + addresses.

    Returns a de-duplicated, order-preserving list (cashtags first, then
    addresses). Blue-chips are dropped — everyone mentions them.
    """
    text = str(text or "")
    out: list[str] = []
    seen: set[str] = set()
    for sym in _CASHTAG_RE.findall(text):
        up = sym.upper()
        if up in _BLUECHIP or f"${up}" in seen:
            continue
        seen.add(f"${up}")
        out.append(f"${up}")
    for addr in _TOKEN_ADDRESS_RE.findall(text):
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


# ══════════════════════════════════════════════════════════════════════════
# DEDUP LEDGER — backend-owned SQLite, tweet_id PRIMARY KEY
# ══════════════════════════════════════════════════════════════════════════


def _dedup_connect() -> sqlite3.Connection | None:
    """Open (and initialize) the dedup ledger; None when unavailable."""
    try:
        os.makedirs(os.path.dirname(_DEDUP_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(_DEDUP_DB_PATH, timeout=5)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS x_shill_seen ("
            "tweet_id TEXT PRIMARY KEY, handle TEXT NOT NULL DEFAULT '', seen_at TEXT NOT NULL)"
        )
        return conn
    except (OSError, sqlite3.Error) as exc:
        logger.debug("x_shill_ingest:dedup_db_open_failed err=%s", str(exc)[:120])
        return None


def _is_seen(conn: sqlite3.Connection, tweet_id: str) -> bool:
    try:
        row = conn.execute("SELECT 1 FROM x_shill_seen WHERE tweet_id = ?", (tweet_id,)).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _mark_seen(conn: sqlite3.Connection, tweet_id: str, handle: str) -> None:
    from datetime import UTC, datetime

    try:
        conn.execute(
            "INSERT OR IGNORE INTO x_shill_seen (tweet_id, handle, seen_at) VALUES (?, ?, ?)",
            (str(tweet_id)[:64], str(handle)[:60], datetime.now(UTC).isoformat()),
        )
        # Bounded growth: prune oldest rows past the cap.
        conn.execute(
            "DELETE FROM x_shill_seen WHERE rowid NOT IN (SELECT rowid FROM x_shill_seen ORDER BY rowid DESC LIMIT ?)",
            (_DEDUP_MAX_ROWS,),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.debug("x_shill_ingest:dedup_mark_failed err=%s", str(exc)[:120])


# ══════════════════════════════════════════════════════════════════════════
# HANDLE RESOLUTION — tracked KOLs from the bot scorecard (fail-open)
# ══════════════════════════════════════════════════════════════════════════


def _resolve_handles(handles: list[str] | None) -> list[str]:
    """Explicit handles, else top-scored KOL handles from the bot scorecard."""
    if handles:
        return [str(h).strip().lstrip("@") for h in handles if str(h).strip()]
    try:
        from app.databus import kol_bot_store as kbs

        scorecard = kbs.load_scorecard(limit=MAX_HANDLES_PER_RUN, order="score")
        return [
            str(e.get("handle") or "").strip().lstrip("@") for e in scorecard.get("scorecard", []) if e.get("handle")
        ]
    except Exception as exc:  # fail-open by contract
        logger.debug("x_shill_ingest:handle_resolve_failed err=%s", str(exc)[:120])
        return []


# ══════════════════════════════════════════════════════════════════════════
# INGEST — pull → extract → dedup → persist
# ══════════════════════════════════════════════════════════════════════════


async def ingest_x_shill(
    handles: list[str] | None = None,
    *,
    tweets_per_handle: int = 10,
) -> dict[str, Any]:
    """One ingest pass: KOL tweets → shill evidence (Qdrant + SQLite).

    Idempotent on tweet ID. Budget-bounded by the ApiPass governor inside
    ``apipass_x`` (≤ MAX_CALLS_PER_HOUR task creations; polling is free), so
    an over-eager schedule degrades to fewer pulls, never a bill shock.
    Returns a summary dict; never raises.
    """
    summary: dict[str, Any] = {
        "handles_polled": 0,
        "tweets_seen": 0,
        "duplicates": 0,
        "evidence_written": 0,
        "tokens_found": 0,
        "errors": [],
    }
    if not apipass_x.api_key_configured():
        logger.debug("x_shill_ingest:skipped_no_key")
        summary["errors"].append("apipass_key_not_configured")
        return summary

    clean_handles = _resolve_handles(handles)
    if not clean_handles:
        logger.debug("x_shill_ingest:no_handles")
        return summary

    conn = _dedup_connect()
    try:
        for handle in clean_handles[: max(1, MAX_HANDLES_PER_RUN)]:
            summary["handles_polled"] += 1
            try:
                tweets = await apipass_x.fetch_user_tweets(handle, limit=tweets_per_handle)
            except Exception as exc:  # per-handle fail-open
                logger.debug("x_shill_ingest:fetch_failed handle=%s err=%s", handle, str(exc)[:120])
                continue
            for tweet in tweets or []:
                tweet_id = str(tweet.get("tweet_id") or "").strip()
                text = str(tweet.get("text") or "")
                if not tweet_id:
                    continue
                summary["tweets_seen"] += 1
                if conn is not None and _is_seen(conn, tweet_id):
                    summary["duplicates"] += 1
                    continue
                tokens = extract_token_signals(text)
                if not tokens:
                    # Still mark seen — a token-less tweet never becomes evidence.
                    if conn is not None:
                        _mark_seen(conn, tweet_id, handle)
                    continue
                summary["tokens_found"] += len(tokens)
                saved = await write_evidence(
                    {
                        "token": tokens[0],
                        "chain": "ticker" if tokens[0].startswith("$") else "evm",
                        "account": handle,
                        "x_url": tweet.get("url") or "",
                        "image_path": "",  # screenshots are a separate capture lane
                        "text": text,
                        "source": "x",
                    }
                )
                if saved:
                    summary["evidence_written"] += 1
                if conn is not None:
                    _mark_seen(conn, tweet_id, handle)
    finally:
        if conn is not None:
            conn.close()

    logger.info(
        "x_shill_ingest:complete handles=%d tweets=%d dup=%d evidence=%d",
        summary["handles_polled"],
        summary["tweets_seen"],
        summary["duplicates"],
        summary["evidence_written"],
    )
    return summary


__all__ = [
    "extract_token_signals",
    "ingest_x_shill",
]
