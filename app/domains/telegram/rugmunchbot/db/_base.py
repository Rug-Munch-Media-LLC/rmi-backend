#!/usr/bin/env python3
"""Shared SQLite connection/client for the rugmunchbot DB layer.

Owns the connection factory (``get_db``), schema bootstrap (``init_db``) and
DB path resolution (``_resolve_db_path``). The ``DB_PATH`` attribute itself is
owned by the package shim (``db/__init__.py``) so tests can keep patching it
via ``monkeypatch.setattr(db, "DB_PATH", ...)`` — ``get_db`` reads the path
dynamically from the package namespace at call time.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import sys
import tempfile

logger = logging.getLogger("app.domains.telegram.rugmunchbot.db")


def _resolve_db_path() -> str:
    """Resolve the bot SQLite path to a writable location.

    Priority:
      1. BOT_DB_PATH env override.
      2. Package-local data dir (default) — used when writable.
      3. Temp dir fallback for read-only containers (/app is read-only).
    """
    override = os.getenv("BOT_DB_PATH", "").strip()
    if override:
        return override
    # The db/ package lives one level below the rugmunchbot package root.
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default = os.path.join(package_root, "data", "bot_users.db")
    try:
        os.makedirs(os.path.dirname(default), exist_ok=True)
        return default
    except OSError:
        fallback_dir = os.path.join(tempfile.gettempdir(), "rmi", "telegram")
        os.makedirs(fallback_dir, exist_ok=True)
        return os.path.join(fallback_dir, "bot_users.db")


_DEFAULT_DB_PATH = _resolve_db_path()


def _db_path() -> str:
    """Return the effective DB path.

    Reads ``DB_PATH`` from the package shim so runtime monkeypatching
    (``monkeypatch.setattr(db, "DB_PATH", ...)``) keeps working after the split.
    """
    pkg = sys.modules.get("app.domains.telegram.rugmunchbot.db")
    if pkg is not None:
        return getattr(pkg, "DB_PATH", _DEFAULT_DB_PATH)
    return _DEFAULT_DB_PATH


def get_db() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            tier TEXT DEFAULT 'free',
            tier_expires INTEGER DEFAULT 0,
            total_scans INTEGER DEFAULT 0,
            total_ai_msgs INTEGER DEFAULT 0,
            created_at INTEGER,
            last_active INTEGER,
            is_banned INTEGER DEFAULT 0,
            referral_code TEXT UNIQUE,
            referred_by TEXT,
            stars_balance INTEGER DEFAULT 0,
            bonus_scans INTEGER DEFAULT 0,
            bonus_ai_msgs INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS weekly_usage (
            user_id INTEGER,
            week_start TEXT,
            scans INTEGER DEFAULT 0,
            ai_msgs INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, week_start)
        );
        CREATE TABLE IF NOT EXISTS watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            token_address TEXT,
            chain TEXT,
            symbol TEXT,
            alert_price REAL,
            alert_direction TEXT,
            created_at INTEGER,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            token_address TEXT,
            chain TEXT,
            scan_type TEXT,
            risk_score REAL,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS star_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            action TEXT,
            telegram_payment_id TEXT,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT NOT NULL,
            event_type TEXT DEFAULT 'all',
            backend_id TEXT,
            is_active INTEGER DEFAULT 1,
            last_ping TEXT,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            token TEXT UNIQUE NOT NULL,
            token_prefix TEXT DEFAULT '',
            name TEXT DEFAULT 'default',
            is_active INTEGER DEFAULT 1,
            last_used INTEGER,
            created_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_weekly_user ON weekly_usage(user_id, week_start);
        CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlists(user_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_scan_user ON scan_history(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_webhooks_user ON webhooks(user_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id, is_active);

        -- v5: bot-wide feature toggles (e.g. bot_enabled, chat_enabled)
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- v5: admin action audit log
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_admin_logs_ts ON admin_logs(created_at);

        -- v5: daily activity counters (messages, commands, RAG queries, AI tokens)
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            messages INTEGER DEFAULT 0,
            commands INTEGER DEFAULT 0,
            rag_queries INTEGER DEFAULT 0,
            ai_tokens INTEGER DEFAULT 0
        );

        -- v5: per-user AI chat history (conversation context, last N messages)
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_chat_history_user ON chat_history(user_id, created_at);

        -- v6: user-submitted scam reports (feeds the known-scam denylist)
        CREATE TABLE IF NOT EXISTS scam_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_address TEXT NOT NULL,
            chain TEXT,
            reason TEXT,
            status TEXT DEFAULT 'open',
            created_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_scam_reports_addr ON scam_reports(token_address);
        CREATE INDEX IF NOT EXISTS idx_scam_reports_user ON scam_reports(user_id, created_at);
    """)
    # Migration: add bonus columns if they don't exist (SQLite safe)
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE users ADD COLUMN bonus_scans INTEGER DEFAULT 0")
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE users ADD COLUMN bonus_ai_msgs INTEGER DEFAULT 0")
    # Migration: alert_triggered_at for tracking when alerts fire
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE watchlists ADD COLUMN alert_triggered_at TEXT")
    # Migration: non-sensitive display prefix for api_tokens (hashed at rest)
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE api_tokens ADD COLUMN token_prefix TEXT DEFAULT ''")
    conn.commit()
    conn.close()
    # One-shot upgrade: hash any legacy plaintext api_tokens.token values.
    try:
        from .users import migrate_plaintext_api_tokens

        migrated = migrate_plaintext_api_tokens()
        if migrated:
            logger.info("api_tokens_migrated_plaintext count=%d", migrated)
    except Exception:
        logger.warning("api_tokens_migration_failed", exc_info=True)
