"""Focused tests: rugmunchbot API tokens are hashed at rest (sha256).

Covers the three contracts introduced by the plaintext fix:
  1. Created tokens are stored as sha256 digests — never the raw secret.
  2. Verification accepts the raw token (constant-time compare) and rejects
     wrong tokens / revoked tokens.
  3. The one-shot migration hashes legacy plaintext rows and is idempotent.
"""

from __future__ import annotations

import hashlib
import secrets
import time

import pytest

from app.domains.telegram.rugmunchbot import db


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the bot DB at a temp file so tests never touch the real DB."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bot_tokens.db"))
    db.init_db()


def _insert_token(
    user_id: int,
    token: str,
    *,
    token_prefix: str = "",
    is_active: int = 1,
) -> int:
    conn = db.get_db()
    cur = conn.execute(
        "INSERT INTO api_tokens (user_id, token, token_prefix, name, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, token, token_prefix, "Token", is_active, int(time.time())),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def _fetch_row(token_id: int) -> dict:
    conn = db.get_db()
    row = conn.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


class TestApiTokenHashing:
    def test_created_token_not_stored_raw(self) -> None:
        user_id = 9101
        raw = f"rmi_{secrets.token_hex(24)}"
        hashed = db.hash_api_token(raw)
        prefix = db.token_prefix(raw)

        token_id = _insert_token(user_id, hashed, token_prefix=prefix)
        row = _fetch_row(token_id)

        assert row["token"] != raw  # raw secret is never at rest
        assert row["token"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert len(row["token"]) == 64
        assert row["token_prefix"] == raw[:12]

    def test_verify_accepts_raw_token(self) -> None:
        user_id = 9102
        raw = f"rmi_{secrets.token_hex(24)}"
        _insert_token(user_id, db.hash_api_token(raw), token_prefix=db.token_prefix(raw))

        verified = db.verify_api_token(raw)
        assert verified is not None
        assert verified["user_id"] == user_id
        assert "token" in verified

    def test_verify_rejects_wrong_token(self) -> None:
        user_id = 9103
        raw = f"rmi_{secrets.token_hex(24)}"
        _insert_token(user_id, db.hash_api_token(raw), token_prefix=db.token_prefix(raw))

        assert db.verify_api_token("rmi_wrongtoken") is None
        assert db.verify_api_token(raw[:-1] + ("0" if raw[-1] != "0" else "1")) is None
        assert db.verify_api_token(raw.upper()) is None  # case change alters the digest
        assert db.verify_api_token("") is None

    def test_verify_rejects_revoked_token(self) -> None:
        user_id = 9104
        raw = f"rmi_{secrets.token_hex(24)}"
        _insert_token(user_id, db.hash_api_token(raw), token_prefix=db.token_prefix(raw), is_active=0)

        assert db.verify_api_token(raw) is None


class TestPlaintextMigration:
    def test_migrate_hashes_plaintext_row(self) -> None:
        user_id = 9105
        raw = f"rmi_{secrets.token_hex(24)}"
        token_id = _insert_token(user_id, raw)  # legacy plaintext, no prefix

        assert _fetch_row(token_id)["token"] == raw

        migrated = db.migrate_plaintext_api_tokens()
        assert migrated == 1

        row = _fetch_row(token_id)
        assert row["token"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert row["token_prefix"] == raw[:12]
        # The migrated token now verifies like a freshly created one.
        assert db.verify_api_token(raw) is not None

    def test_migrate_is_idempotent(self) -> None:
        user_id = 9106
        raw = f"rmi_{secrets.token_hex(24)}"
        token_id = _insert_token(user_id, raw)

        assert db.migrate_plaintext_api_tokens() == 1
        assert db.migrate_plaintext_api_tokens() == 0  # already hashed
        assert _fetch_row(token_id)["token"] != raw

    def test_migrate_skips_already_hashed_rows(self) -> None:
        user_id = 9107
        raw = f"rmi_{secrets.token_hex(24)}"
        token_id = _insert_token(user_id, db.hash_api_token(raw), token_prefix=db.token_prefix(raw))

        assert db.migrate_plaintext_api_tokens() == 0
        assert _fetch_row(token_id)["token"] == db.hash_api_token(raw)
