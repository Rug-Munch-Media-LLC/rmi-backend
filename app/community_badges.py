"""
Community Reputation Badges - Verified Sleuth System
=====================================================
Users vote on whether a token is clean or malicious. If their vote aligns
with the ultimate outcome (rug confirmed / token survives), they earn a
verified sleuth badge. Builds engagement without requiring financial rewards.

Architecture:
  - SQLite-backed vote tracking (lightweight, no extra infra)
  - Badge tiers: Bronze (3+ correct), Silver (10+), Gold (25+), Diamond (50+)
  - API endpoint for voting and badge queries
  - Token outcome resolution via manual/cron trigger
"""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "community_badges.db")

# ── Badge tiers ──────────────────────────────────────────────────
BADGE_TIERS = [
    {"name": "Sleuth", "min_correct": 3, "emoji": "🔍", "color": "#9B59B6"},
    {"name": "Detective", "min_correct": 10, "emoji": "🕵️", "color": "#3498DB"},
    {"name": "Veteran", "min_correct": 25, "emoji": "🎖️", "color": "#F39C12"},
    {"name": "Oracle", "min_correct": 50, "emoji": "🔮", "color": "#E74C3C"},
    {"name": "Legend", "min_correct": 100, "emoji": "👑", "color": "#FFD700"},
]


@dataclass
class UserBadge:
    user_id: str
    total_votes: int = 0
    correct_votes: int = 0
    current_tier: str = "none"
    next_tier: str = "Sleuth"
    votes_needed_for_next: int = 3
    badges_earned: list[str] = field(default_factory=list)
    accuracy_pct: float = 0.0


@dataclass
class TokenVote:
    token_address: str
    chain: str
    voter_id: str
    vote: str  # "clean" or "malicious"
    voted_at: str
    outcome: str | None = None  # None=pending, "clean", "malicious", "rug_confirmed"
    resolved_at: str | None = None
    earned_badge: bool = False


def _get_db() -> sqlite3.Connection:
    """Get or create SQLite DB with tables."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_address TEXT NOT NULL,
            chain TEXT NOT NULL,
            voter_id TEXT NOT NULL,
            vote TEXT NOT NULL CHECK(vote IN ('clean', 'malicious')),
            voted_at TEXT NOT NULL DEFAULT (datetime('now')),
            outcome TEXT CHECK(outcome IN ('clean', 'malicious', 'rug_confirmed', NULL)),
            resolved_at TEXT,
            earned_badge INTEGER DEFAULT 0,
            UNIQUE(token_address, chain, voter_id)
        );

        CREATE TABLE IF NOT EXISTS badges (
            user_id TEXT PRIMARY KEY,
            total_votes INTEGER DEFAULT 0,
            correct_votes INTEGER DEFAULT 0,
            current_tier TEXT DEFAULT 'none',
            earned_tiers TEXT DEFAULT '[]',
            last_updated TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_votes_token ON votes(token_address, chain);
        CREATE INDEX IF NOT EXISTS idx_votes_voter ON votes(voter_id);
        CREATE INDEX IF NOT EXISTS idx_votes_outcome ON votes(outcome);
    """)
    conn.commit()
    return conn


def cast_vote(token_address: str, chain: str, voter_id: str, vote: str) -> dict[str, Any]:
    """Cast a vote on a token. One vote per token per user."""
    if vote not in ("clean", "malicious"):
        return {"error": "Vote must be 'clean' or 'malicious'", "success": False}

    conn = _get_db()
    try:
        # Upsert: replace if exists, insert if new
        conn.execute(
            """INSERT INTO votes (token_address, chain, voter_id, vote)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(token_address, chain, voter_id)
               DO UPDATE SET vote = excluded.vote, voted_at = datetime('now')""",
            (token_address, chain, voter_id, vote),
        )

        # Update badge stats
        total = conn.execute("SELECT COUNT(*) FROM votes WHERE voter_id = ?", (voter_id,)).fetchone()[0]

        # Recalculate correct votes using normalized outcome matching
        correct = conn.execute(
            """SELECT COUNT(*) FROM votes WHERE voter_id = ?
               AND outcome IS NOT NULL
               AND vote = CASE
                   WHEN outcome = 'rug_confirmed' THEN 'malicious'
                   ELSE outcome
               END""",
            (voter_id,),
        ).fetchone()[0]

        tier = _compute_tier(correct)
        earned_tiers = _get_earned_tiers(correct)

        conn.execute(
            """INSERT INTO badges (user_id, total_votes, correct_votes, current_tier, earned_tiers)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
               total_votes = excluded.total_votes,
               correct_votes = excluded.correct_votes,
               current_tier = excluded.current_tier,
               earned_tiers = excluded.earned_tiers,
               last_updated = datetime('now')""",
            (voter_id, total, correct, tier, json.dumps(earned_tiers)),
        )
        conn.commit()

        return {
            "success": True,
            "token_address": token_address,
            "voter_id": voter_id,
            "vote": vote,
            "total_votes": total,
            "correct_votes": correct,
            "current_tier": tier,
        }
    except Exception as e:
        logger.error(f"Vote failed: {e}")
        return {"error": str(e), "success": False}
    finally:
        conn.close()


def resolve_token_outcome(token_address: str, chain: str, outcome: str) -> dict[str, Any]:
    """Resolve a token's outcome and update voter badges. Call when token rugs or survives."""
    if outcome not in ("clean", "malicious", "rug_confirmed"):
        return {
            "error": "Outcome must be 'clean', 'malicious', or 'rug_confirmed'",
            "success": False,
        }

    # Normalize: rug_confirmed = malicious for matching purposes
    match_outcome = "malicious" if outcome == "rug_confirmed" else outcome

    conn = _get_db()
    try:
        now = datetime.now(UTC).isoformat()

        # Update outcome on all votes for this token
        conn.execute(
            "UPDATE votes SET outcome = ?, resolved_at = ? WHERE token_address = ? AND chain = ?",
            (outcome, now, token_address, chain),
        )

        # Mark correct voters
        conn.execute(
            "UPDATE votes SET earned_badge = 1 WHERE token_address = ? AND chain = ? AND vote = ?",
            (token_address, chain, match_outcome),
        )

        # Recalculate badges for all voters of this token
        voters = conn.execute(
            "SELECT DISTINCT voter_id FROM votes WHERE token_address = ? AND chain = ?",
            (token_address, chain),
        ).fetchall()

        for row in voters:
            vid = row[0]
            total = conn.execute("SELECT COUNT(*) FROM votes WHERE voter_id = ?", (vid,)).fetchone()[0]
            correct = conn.execute(
                """SELECT COUNT(*) FROM votes WHERE voter_id = ?
                   AND outcome IS NOT NULL
                   AND vote = CASE
                       WHEN outcome = 'rug_confirmed' THEN 'malicious'
                       ELSE outcome
                   END""",
                (vid,),
            ).fetchone()[0]
            tier = _compute_tier(correct)
            earned_tiers = _get_earned_tiers(correct)
            conn.execute(
                """INSERT INTO badges (user_id, total_votes, correct_votes, current_tier, earned_tiers)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   total_votes = excluded.total_votes,
                   correct_votes = excluded.correct_votes,
                   current_tier = excluded.current_tier,
                   earned_tiers = excluded.earned_tiers,
                   last_updated = datetime('now')""",
                (vid, total, correct, tier, json.dumps(earned_tiers)),
            )

        conn.commit()
        return {
            "success": True,
            "token_address": token_address,
            "chain": chain,
            "outcome": outcome,
            "voters_updated": len(voters),
            "resolved_at": now,
        }
    except Exception as e:
        logger.error(f"Resolve failed: {e}")
        return {"error": str(e), "success": False}
    finally:
        conn.close()


def get_user_badge(user_id: str) -> UserBadge | None:
    """Get a user's badge profile."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM badges WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return UserBadge(user_id=user_id)

        earned = json.loads(row["earned_tiers"]) if row["earned_tiers"] else []
        accuracy = (row["correct_votes"] / row["total_votes"] * 100) if row["total_votes"] > 0 else 0.0

        next_tier, needed = _next_tier(row["correct_votes"])

        return UserBadge(
            user_id=user_id,
            total_votes=row["total_votes"],
            correct_votes=row["correct_votes"],
            current_tier=row["current_tier"],
            next_tier=next_tier,
            votes_needed_for_next=needed,
            badges_earned=earned,
            accuracy_pct=round(accuracy, 1),
        )
    except Exception as e:
        logger.error(f"Badge lookup failed: {e}")
        return UserBadge(user_id=user_id)
    finally:
        conn.close()


def get_token_votes(token_address: str, chain: str) -> dict[str, Any]:
    """Get all votes for a token with summary."""
    conn = _get_db()
    try:
        votes = conn.execute(
            "SELECT * FROM votes WHERE token_address = ? AND chain = ? ORDER BY voted_at DESC",
            (token_address, chain),
        ).fetchall()

        clean = sum(1 for v in votes if v["vote"] == "clean")
        malicious = sum(1 for v in votes if v["vote"] == "malicious")
        total = len(votes)

        consensus = clean / total * 100 if total > 0 else 50

        return {
            "token_address": token_address,
            "chain": chain,
            "total_votes": total,
            "clean_votes": clean,
            "malicious_votes": malicious,
            "community_consensus": round(consensus, 1),
            "consensus_label": "clean" if consensus > 60 else ("malicious" if consensus < 40 else "divided"),
            "outcome": votes[0]["outcome"] if total > 0 else None,
            "votes": [dict(v) for v in votes],
        }
    except Exception as e:
        logger.error(f"Token votes lookup failed: {e}")
        return {"error": str(e), "token_address": token_address, "total_votes": 0}
    finally:
        conn.close()


def get_leaderboard(limit: int = 20) -> list[dict[str, Any]]:
    """Get top sleuths by correct votes."""
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM badges ORDER BY correct_votes DESC LIMIT ?", (limit,)).fetchall()
        return [
            {
                "user_id": r["user_id"],
                "tier": r["current_tier"],
                "correct": r["correct_votes"],
                "total": r["total_votes"],
                "accuracy": round(r["correct_votes"] / r["total_votes"] * 100, 1) if r["total_votes"] > 0 else 0,
                "badges": json.loads(r["earned_tiers"]) if r["earned_tiers"] else [],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Leaderboard failed: {e}")
        return []
    finally:
        conn.close()


# ── Internal helpers ──────────────────────────────────────────────


def _compute_tier(correct: int) -> str:
    """Compute badge tier from correct vote count."""
    tier = "none"
    for t in reversed(BADGE_TIERS):
        if correct >= t["min_correct"]:
            tier = t["name"]
            break
    return tier


def _get_earned_tiers(correct: int) -> list[str]:
    """Get list of all badge names earned."""
    return [t["name"] for t in BADGE_TIERS if correct >= t["min_correct"]]


def _next_tier(correct: int) -> tuple:
    """Get the next tier and votes needed."""
    for t in BADGE_TIERS:
        if correct < t["min_correct"]:
            return t["name"], t["min_correct"] - correct
    return BADGE_TIERS[-1]["name"], 0  # Max tier
