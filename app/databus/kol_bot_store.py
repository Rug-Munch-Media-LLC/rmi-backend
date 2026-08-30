"""Bot-side KOL store bridge (read-only).

Reads the KOL intelligence that rugmunchbot persists into ``bot_users.db``
(``kol_identities`` / ``kol_aliases`` / ``kol_scores`` / ``kol_calls``) plus
the curated Wall of Shame file ``data/kol/wall_of_shame.json``, and exposes it to
the backend API layer.

Design contract (mirrors ``app/routers/admin/bot_admin.py``):
  - Read-only sqlite access (``file:...?mode=ro``) — never blocks or
    migrates the live bot DB, never creates files.
  - Path resolution degrades through the same candidate list the admin
    router uses (env override → vendored bot copy → repo checkout).
  - Every loader returns empty structures on any failure — never raises.

Data notes:
  - ``kol_scores`` tracks call hygiene (total/deleted/shill/scam-token
    calls) and a 0-100 trust score. Wins/losses are *derived*:
    losses = deleted + shill + scam-token calls, wins = the remainder.
  - Per-KOL PnL is NOT tracked in the bot DB — ``profit`` is always
    ``None`` and the API says so explicitly (no fabricated numbers).
  - ``wall_of_shame.json`` is curated by the bot's ``kol_ingest`` pipeline
    (rule-matched serial deployers / shill groups with evidence URLs).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger("kol_bot_store")

#: Candidate locations for the bot's SQLite DB (first existing wins).
_BOT_DB_CANDIDATES: tuple[str, ...] = (
    os.getenv("BOT_DB_PATH", "").strip(),
    "/srv/work/repos/rmi-backend/app/domains/telegram/rugmunchbot/data/bot_users.db",
    "/srv/work/repos/rugmunchbot/src/rugmunchbot/data/bot_users.db",
    "/tmp/rmi/telegram/bot_users.db",  # nosec B108 — known bot DB mirror path, stable inter-process lookup
)

#: Candidate locations for the curated Wall of Shame file.
_WALL_OF_SHAME_CANDIDATES: tuple[str, ...] = (
    os.getenv("KOL_WALL_OF_SHAME_PATH", "").strip(),
    "/srv/work/repos/rugmunchbot/src/rugmunchbot/data/kol/wall_of_shame.json",
    "/srv/work/repos/rmi-backend/app/domains/telegram/rugmunchbot/data/kol/wall_of_shame.json",
)


def _has_kol_tables(path: Path) -> bool:
    """True when the DB actually carries the KOL tables (some bot DB
    copies predate the kol_* schema — file existence is not enough)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            conn.close()
        return {"kol_scores", "kol_identities"} <= names
    except (sqlite3.Error, OSError):
        return False


def resolve_bot_db_path() -> Path | None:
    """First bot DB path that exists AND carries the kol_* tables.

    [REVIEW] Multiple bot_users.db copies exist (vendored bot dir, bot repo
    checkout, /tmp mirror); older copies lack the kol_* schema, so each
    candidate is probed for ``kol_scores`` + ``kol_identities`` before use.
    Set ``BOT_DB_PATH`` to pin a specific file.
    """
    fallback: Path | None = None
    for cand in _BOT_DB_CANDIDATES:
        if not cand:
            continue
        path = Path(cand)
        if not path.exists():
            continue
        if _has_kol_tables(path):
            return path
        if fallback is None:
            fallback = path
    return fallback


def _connect() -> sqlite3.Connection | None:
    """Read-only connection to the bot DB (never creates/blocks)."""
    path = resolve_bot_db_path()
    if path is None:
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except (sqlite3.Error, OSError) as e:
        logger.warning("kol_bot_store: bot DB open failed %s: %s", path, str(e)[:120])
        return None


def _norm(handle: str | None) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _first_wallet(raw: Any) -> str:
    """First wallet address from the identities ``wallets`` JSON column."""
    try:
        wallets = json.loads(raw) if isinstance(raw, str) else (raw or [])
        for w in wallets if isinstance(wallets, list) else []:
            if isinstance(w, str) and w.strip():
                return w.strip()
            if isinstance(w, dict) and str(w.get("address") or "").strip():
                return str(w["address"]).strip()
    except (TypeError, ValueError):
        pass
    return ""


# ═══════════════════════════════════════════════════════════════════════
# Scorecard (kol_scores ⋈ kol_identities)
# ═══════════════════════════════════════════════════════════════════════


def load_scorecard(
    limit: int = 100,
    offset: int = 0,
    *,
    min_calls: int = 0,
    order: str = "score",
) -> dict[str, Any]:
    """Ranked KOL scorecard from ``kol_scores`` joined with ``kol_identities``.

    Ranking: ``score`` (trust desc) or ``calls`` (activity desc). Wins and
    losses are derived from call-hygiene counters; ``profit`` is always
    ``None`` (the bot DB does not track per-KOL PnL).
    """
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    min_calls = max(0, int(min_calls))
    order_sql = "s.score DESC, s.total_calls DESC" if order == "score" else "s.total_calls DESC, s.score DESC"
    sql = f"""
        SELECT s.handle, s.total_calls, s.deleted_calls, s.shill_calls,
               s.scam_token_calls, s.tokens_mentioned, s.score, s.grade,
               s.updated_at, i.kol_id, i.name, i.screen_name, i.category,
               i.risk_level, i.wallets, i.scammer_group
        FROM kol_scores s
        LEFT JOIN kol_identities i ON lower(i.screen_name) = lower(s.handle)
        WHERE s.total_calls >= ?
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """  # nosec B608 — order_sql is a fixed internal constant, not user input
    try:
        conn = _connect()
        if conn is None:
            return {"scorecard": [], "total": 0, "source": "bot_users_db"}
        try:
            total = conn.execute("SELECT COUNT(*) FROM kol_scores WHERE total_calls >= ?", (min_calls,)).fetchone()[0]
            rows = conn.execute(sql, (min_calls, limit, offset)).fetchall()
        finally:
            conn.close()
        entries = []
        for r in rows:
            losses = int(r["deleted_calls"] or 0) + int(r["shill_calls"] or 0) + int(r["scam_token_calls"] or 0)
            wins = max(0, int(r["total_calls"] or 0) - losses)
            entries.append(
                {
                    "handle": _norm(r["handle"]),
                    "display_name": r["name"] or r["screen_name"] or r["handle"],
                    "wallet": _first_wallet(r["wallets"]) or None,
                    "score": round(float(r["score"] or 0), 2),
                    "grade": r["grade"],
                    "wins": wins,
                    "losses": losses,
                    "total_calls": int(r["total_calls"] or 0),
                    "deleted_calls": int(r["deleted_calls"] or 0),
                    "shill_calls": int(r["shill_calls"] or 0),
                    "scam_token_calls": int(r["scam_token_calls"] or 0),
                    "tokens_mentioned": int(r["tokens_mentioned"] or 0),
                    "profit": None,  # not tracked in bot DB — never fabricate
                    "category": r["category"],
                    "risk_level": r["risk_level"],
                    "scammer_group": r["scammer_group"] or None,
                    "updated_at": r["updated_at"],
                }
            )
        return {"scorecard": entries, "total": total, "limit": limit, "offset": offset, "source": "bot_users_db"}
    except sqlite3.Error as e:
        logger.warning("kol_bot_store: scorecard query failed: %s", str(e)[:160])
        return {"scorecard": [], "total": 0, "limit": limit, "offset": offset, "source": "bot_users_db"}


# ═══════════════════════════════════════════════════════════════════════
# Wall of Shame (data/kol/wall_of_shame.json)
# ═══════════════════════════════════════════════════════════════════════


def load_wall_of_shame(severity: str = "") -> dict[str, Any]:
    """Wall of Shame entries: handle, wallet, evidence_url, type.

    Reads the bot's curated ``wall_of_shame.json`` (rule-matched scammers,
    rug pullers, ponzi founders, celebrity touts — each with an evidence
    URL). Degrades to an empty wall when the file is missing/corrupt.
    """
    sev = severity.strip().lower()
    for cand in _WALL_OF_SHAME_CANDIDATES:
        path = Path(cand) if cand else None
        if path is None or not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            records = raw.get("wall_of_shame", []) if isinstance(raw, dict) else raw
            if not isinstance(records, list):
                continue
            entries = [
                {
                    "handle": str(b.get("handle") or b.get("handle_id") or "").strip(),
                    "handle_id": b.get("handle_id"),
                    "wallet": str(b.get("wallet") or "").strip() or None,
                    "wallets": b.get("wallets") or [],
                    "type": b.get("category"),
                    "severity": b.get("severity"),
                    "risk_level": b.get("risk_level"),
                    "group": b.get("group"),
                    "evidence_url": b.get("evidence_url"),
                }
                for b in records
                if isinstance(b, dict) and (b.get("handle") or b.get("handle_id"))
            ]
            if sev:
                entries = [e for e in entries if str(e.get("severity") or "").lower() == sev]
            return {
                "wall_of_shame": entries,
                "total": len(entries),
                "severity": sev or None,
                "source": "wall_of_shame_json",
            }
        except (OSError, ValueError) as e:
            logger.warning("kol_bot_store: wall_of_shame unreadable %s: %s", path, str(e)[:120])
    return {"wall_of_shame": [], "total": 0, "severity": sev or None, "source": "wall_of_shame_json"}


# ═══════════════════════════════════════════════════════════════════════
# Per-handle bot profile (enrichment for /kol/{handle})
# ═══════════════════════════════════════════════════════════════════════


def load_bot_profile(handle: str) -> dict[str, Any] | None:
    """Bot-DB view of one handle: identity + score + recent calls, or None."""
    key = _norm(handle)
    if not key:
        return None
    try:
        conn = _connect()
        if conn is None:
            return None
        try:
            ident = conn.execute("SELECT * FROM kol_identities WHERE lower(screen_name) = ? LIMIT 1", (key,)).fetchone()
            if ident is None:
                alias = conn.execute("SELECT kol_id FROM kol_aliases WHERE lower(alias) = ? LIMIT 1", (key,)).fetchone()
                if alias is not None:
                    ident = conn.execute(
                        "SELECT * FROM kol_identities WHERE kol_id = ? LIMIT 1", (alias["kol_id"],)
                    ).fetchone()
            score = conn.execute("SELECT * FROM kol_scores WHERE lower(handle) = ? LIMIT 1", (key,)).fetchone()
            calls = conn.execute(
                "SELECT token_symbol, call_type, result, is_deleted, posted_at, source_url "
                "FROM kol_calls WHERE lower(handle) = ? ORDER BY captured_at DESC LIMIT 10",
                (key,),
            ).fetchall()
        finally:
            conn.close()
        if ident is None and score is None:
            return None
        losses = 0
        wins = 0
        if score is not None:
            losses = (
                int(score["deleted_calls"] or 0) + int(score["shill_calls"] or 0) + int(score["scam_token_calls"] or 0)
            )
            wins = max(0, int(score["total_calls"] or 0) - losses)
        profile: dict[str, Any] = {
            "kol_id": ident["kol_id"] if ident else None,
            "category": ident["category"] if ident else None,
            "risk_level": ident["risk_level"] if ident else None,
            "scammer_group": (ident["scammer_group"] if ident else None) or None,
            "wallet": _first_wallet(ident["wallets"]) if ident else None,
            "bot_score": round(float(score["score"]), 2) if score is not None else None,
            "grade": score["grade"] if score is not None else None,
            "total_calls": int(score["total_calls"] or 0) if score is not None else 0,
            "wins": wins,
            "losses": losses,
            "profit": None,  # not tracked in bot DB
            "recent_calls": [dict(c) for c in calls],
            "source": "bot_users_db",
        }
        return profile
    except sqlite3.Error as e:
        logger.warning("kol_bot_store: profile lookup failed %s: %s", key, str(e)[:160])
        return None


# ═══════════════════════════════════════════════════════════════════════
# Cheap social-sentiment derivation (non-Pro path)
# ═══════════════════════════════════════════════════════════════════════


def derive_kol_sentiment_docs(limit: int = 2000) -> list[dict[str, Any]]:
    """Build embeddable sentiment docs from bot KOL data (no paid API).

    Two doc kinds:
      - ``kol_sentiment`` — one per scored handle: trust score, grade,
        shill/scam ratios, derived wins/losses. Sentiment polarity comes
        from the hygiene counters (scam/shill-heavy → negative signal).
      - ``call_sentiment`` — one per documented non-unknown call result
        (kol_calls): token, handle, result. Documented loss calls are
        strong negative token sentiment.

    This replaces the paid Pro sentiment feed as the population source
    for the Qdrant ``social_sentiment`` collection.
    """
    docs: list[dict[str, Any]] = []
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        conn = _connect()
        if conn is None:
            return docs
        try:
            rows = conn.execute(
                "SELECT s.handle, s.total_calls, s.deleted_calls, s.shill_calls, "
                "s.scam_token_calls, s.score, s.grade, i.category, i.risk_level, "
                "i.scammer_group, i.wallets "
                "FROM kol_scores s LEFT JOIN kol_identities i "
                "ON lower(i.screen_name) = lower(s.handle) "
                "ORDER BY s.score DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            call_rows = conn.execute(
                "SELECT handle, token_symbol, token_address, chain, result, is_deleted, "
                "posted_at, source_url FROM kol_calls WHERE result != 'unknown' "
                "ORDER BY captured_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()

        for r in rows:
            handle = _norm(r["handle"])
            total = int(r["total_calls"] or 0)
            bad = int(r["deleted_calls"] or 0) + int(r["shill_calls"] or 0) + int(r["scam_token_calls"] or 0)
            wins = max(0, total - bad)
            score = float(r["score"] or 0)
            # Polarity: trust score centered at 50, penalized by bad-call share.
            bad_share = bad / total if total > 0 else 0.0
            polarity = max(-1.0, min(1.0, (score - 50.0) / 50.0 - 0.5 * bad_share))
            label = "bullish" if polarity > 0.15 else "bearish" if polarity < -0.15 else "neutral"
            text = (
                f"KOL @{handle} social sentiment signal (derived from tracked call record, "
                f"not a paid feed): trust score {score:.1f}/100 grade {r['grade']}, "
                f"{total} tracked calls ({wins} clean, {bad} flagged: "
                f"{r['deleted_calls']} deleted, {r['shill_calls']} shill, "
                f"{r['scam_token_calls']} scam-token). Category {r['category'] or 'unknown'}, "
                f"risk level {r['risk_level'] or 'watch'}. "
                f"Aggregate sentiment polarity {polarity:.2f} ({label})."
            )
            docs.append(
                {
                    "id": f"kol_sentiment:{handle}:{today}",
                    "text": text[:2000],
                    "metadata": {
                        "kind": "kol_sentiment",
                        "handle": handle,
                        "score": round(score, 2),
                        "grade": r["grade"],
                        "polarity": round(polarity, 3),
                        "sentiment_label": label,
                        "wins": wins,
                        "losses": bad,
                        "total_calls": total,
                        "category": r["category"],
                        "risk_level": r["risk_level"],
                        "scammer_group": r["scammer_group"] or "",
                        "wallet": _first_wallet(r["wallets"]) or "",
                        "signal": "derived",
                        "ts": today,
                        "source": "bot_users_db",
                    },
                }
            )

        for c in call_rows:
            token = str(c["token_symbol"] or "").strip().upper()
            if not token:
                continue
            chandle = _norm(c["handle"])
            result = str(c["result"] or "unknown")
            polarity = -1.0 if result == "loss" else 1.0 if result == "win" else 0.0
            text = (
                f"Documented KOL call: @{chandle} called {token} "
                f"({c['chain'] or 'unknown chain'}), outcome {result}. "
                f"{'Deletion detected.' if c['is_deleted'] else 'Post still live.'} "
                f"Call sentiment polarity {polarity:.1f}."
            )
            docs.append(
                {
                    "id": f"call_sentiment:{chandle}:{token}:{today}",
                    "text": text[:2000],
                    "metadata": {
                        "kind": "call_sentiment",
                        "handle": chandle,
                        "token": token,
                        "token_address": c["token_address"] or "",
                        "chain": c["chain"] or "",
                        "result": result,
                        "is_deleted": bool(c["is_deleted"]),
                        "polarity": polarity,
                        "posted_at": c["posted_at"],
                        "source_url": c["source_url"] or "",
                        "signal": "derived",
                        "ts": today,
                        "source": "bot_users_db",
                    },
                }
            )
        return docs
    except sqlite3.Error as e:
        logger.warning("kol_bot_store: sentiment derivation failed: %s", str(e)[:160])
        return docs
