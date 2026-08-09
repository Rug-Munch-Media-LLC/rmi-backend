"""Chat SQLite persistence helpers shared by chat routers.

Extracted from app/routers/chat.py so the history/stats handlers can reuse
the same connection helpers without circular imports.

The container runs read-only except /tmp and /dev/shm, so /app/data is not
writable at runtime. Allow override via CHAT_DB_PATH; fall back to the
repo data dir when writable, otherwise a per-app temp dir.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile

# ── DB path ──


def _resolve_db_path() -> str:
    override = os.getenv("CHAT_DB_PATH", "").strip()
    if override:
        return override
    repo_data = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "rmi.db")
    try:
        os.makedirs(os.path.dirname(repo_data), exist_ok=True)
        return repo_data
    except OSError:
        fallback_dir = os.path.join(tempfile.gettempdir(), "rmi")
        os.makedirs(fallback_dir, exist_ok=True)
        return os.path.join(fallback_dir, "chat.db")


DB_PATH = _resolve_db_path()


# ── Database helpers ──
def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables() -> None:
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_messages(user_id, created_at)")
    conn.commit()
    conn.close()


# Router must mount even if the DB layer is unavailable (e.g. read-only
# filesystem) — the handlers degrade gracefully per-request.
with contextlib.suppress(Exception):
    _ensure_tables()
