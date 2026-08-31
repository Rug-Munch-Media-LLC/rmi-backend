"""Shill evidence store — ``shill_evidence`` rows + Qdrant ``threat_intel_v2``.

A screenshot is proof that survives a deleted tweet. This module is the
backend form of the bot's ``_persist_shill_evidence``: it stores a shill's
screenshot reference + vision-OCR text (a) in the ``shill_evidence`` SQLite
table (read-only bridge into the bot's ``bot_users.db``) and (b) as a Qdrant
point in the scam-intel collection (``threat_intel_v2``) so the API can answer
"who shilled $X / what did they post".

Design contract:
  * Reads NEVER open a writable connection to the live bot DB — they use a
    read-only sqlite URI (``file:...?mode=ro``) through the same candidate
    path list the ``kol_bot_store`` bridge uses, so the backend never blocks
    or migrates the bot DB.
  * Writes are fail-open and best-effort: the Qdrant upsert is the canonical
    evidence write; the DB insert is an opportunistic mirror and is skipped
    when the bot DB is not writable. Any error is logged, never raised.
  * Every loader returns empty collections on failure — never raises.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.logging import get_logger
from app.utils.media_urls import public_image_url

logger = get_logger("shill_evidence")

#: Canonical Qdrant URL + physical collection for shill/screenshot evidence.
#: 2026-09-01: the old default ``http://127.0.0.1:6334`` is unreachable inside
#: the serving container (real Qdrant resolves as ``rmi-qdrant-v2:6333``) and
#: the legacy ``RMI_QDRANT_URL`` env was never set in production, so the
#: evidence Qdrant upsert silently no-op'd. Resolve against the fleet canonical
#: ``QDRANT_URL`` chain (same as ``app.integrations.rugbrain_client``) and keep
#: ``RMI_QDRANT_URL`` as a legacy override. The port is 6333, not 6334.
_QDRANT_URL = os.getenv("RMI_QDRANT_URL") or os.getenv(
    "QDRANT_URL", os.getenv("QDRANT_FALLBACK", "http://rmi-qdrant-v2:6333")
).rstrip("/")
_SHILL_EVIDENCE_COLLECTION = os.getenv("RMI_SHILL_EVIDENCE_COLLECTION", "threat_intel_v2")
#: Max OCR text chars persisted per evidence row (bounded payload).
_SHILL_OCR_MAX_CHARS = 1200

#: Candidate locations for the bot's SQLite DB (first existing wins). Mirrors
#: ``app.databus.kol_bot_store`` so evidence + KOL intel resolve to one DB.
_BOT_DB_CANDIDATES: tuple[str, ...] = (
    os.getenv("BOT_DB_PATH", "").strip(),
    "/srv/work/repos/rmi-backend/app/domains/telegram/rugmunchbot/data/bot_users.db",
    "/srv/work/repos/rugmunchbot/src/rugmunchbot/data/bot_users.db",
    "/tmp/rmi/telegram/bot_users.db",
)


def _resolve_db_path() -> Path | None:
    """First bot DB path that carries the ``shill_evidence`` table (or None)."""
    for cand in _BOT_DB_CANDIDATES:
        if not cand:
            continue
        path = Path(cand)
        if not path.is_file():
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
            try:
                names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            finally:
                conn.close()
            if "shill_evidence" in names:
                return path
        except sqlite3.Error:
            continue
    return None


# ══════════════════════════════════════════════════════════════════════════
# READ — shill_evidence rows from the bot DB (read-only bridge)
# ══════════════════════════════════════════════════════════════════════════


def _norm_row(row: sqlite3.Row) -> dict[str, Any]:
    image_path = row["image_path"]
    return {
        "id": row["id"],
        "token": row["token"],
        "chain": row["chain"],
        "account": row["account"],
        "x_url": row["x_url"],
        "image_path": image_path,
        # Public, rate-limited URL for the screenshot ("" when unusable) so
        # feed/website payloads can render the evidence thumbnail directly.
        "image_url": public_image_url(image_path),
        "ocr_text": row["ocr_text"],
        "captured_at": row["captured_at"],
    }


def read_shill_evidence(
    *,
    token: str = "",
    chain: str = "",
    account: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return ``shill_evidence`` rows, optionally filtered. Fail-open ``[]``.

    Reads the ``shill_evidence`` table from the live bot DB read-only. Any
    error (missing DB, missing table, bad path, corrupt row) → ``[]``.
    """
    path = _resolve_db_path()
    if path is None:
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            query = "SELECT * FROM shill_evidence WHERE 1=1"
            params: list[Any] = []
            if token:
                query += " AND token LIKE ?"
                params.append(f"%{token}%")
            if chain:
                query += " AND chain = ?"
                params.append(chain)
            if account:
                query += " AND account LIKE ?"
                params.append(f"%{account}%")
            query += " ORDER BY id DESC LIMIT ?"
            params.append(max(1, int(limit)))
            rows = conn.execute(query, params).fetchall()
            return [_norm_row(r) for r in rows]
        finally:
            conn.close()
    except (OSError, sqlite3.Error, TypeError) as exc:
        logger.debug("shill_evidence:read_failed err=%s", str(exc)[:120])
        return []


def list_shill_evidence(limit: int = 200) -> list[dict[str, Any]]:
    """All shill evidence rows, newest first. Fail-open ``[]``."""
    return read_shill_evidence(limit=limit)


# ══════════════════════════════════════════════════════════════════════════
# WRITE — canonical Qdrant upsert + opportunistic DB mirror (fail-open)
# ══════════════════════════════════════════════════════════════════════════


async def _embed_text(text: str) -> list[float] | None:
    """Embed *text* via the backend embedder; None when unavailable."""
    try:
        from app.embedding_router import embed_single

        vec, _meta = await embed_single(text)
        if isinstance(vec, list) and vec and isinstance(vec[0], (int, float)):
            return [float(x) for x in vec]
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_evidence:embed_failed err=%s", str(exc)[:120])
    return None


def _db_insert(token: str, chain: str, account: str, x_url: str, image_path: str, ocr: str) -> bool:
    """Best-effort mirror into the bot's ``shill_evidence`` table (fail-open)."""
    path = _resolve_db_path()
    if path is None:
        return False
    try:
        conn = sqlite3.connect(str(path), timeout=3)
        try:
            conn.execute(
                "INSERT INTO shill_evidence (token, chain, account, x_url, image_path, ocr_text, captured_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(token)[:80],
                    str(chain)[:40],
                    str(account)[:60],
                    str(x_url)[:240],
                    str(image_path)[:400],
                    str(ocr)[:_SHILL_OCR_MAX_CHARS],
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:  # fail-open by contract
        logger.debug("shill_evidence:db_insert_failed err=%s", str(exc)[:120])
        return False


async def write_evidence(record: dict[str, Any]) -> bool:
    """Persist one screenshot reference + OCR text to Qdrant + DB.

    ``record`` carries ``token``/``chain``/``account``/``x_url``/
    ``image_path``/``text`` (the capture output). The Qdrant upsert is the
    canonical evidence write; the DB insert is an opportunistic mirror.
    Returns True when the canonical write landed. Never raises.
    """
    token = str(record.get("token") or "")
    chain = str(record.get("chain") or "")
    account = str(record.get("account") or "")
    x_url = str(record.get("x_url") or record.get("url") or "")
    image_path = str(record.get("image_path") or "")
    ocr = str(record.get("text") or record.get("ocr_text") or "")[:_SHILL_OCR_MAX_CHARS]
    # Capture origin ("x" | "telegram"); empty → legacy account-derived default.
    source = str(record.get("source") or "").strip().lower()

    db_saved = _db_insert(token, chain, account, x_url, image_path, ocr)

    if not (x_url or ocr):
        return bool(db_saved)

    try:
        content = (f"Source: {account or 'unknown'}\nToken: {token}\nChain: {chain}\nURL: {x_url}\nOCR: {ocr}").strip()
        digest = hashlib.sha256(f"{token}:{x_url}:{account}".encode()).hexdigest()
        point_id = uuid.UUID(int=int(digest, 16) % (2**96), version=4)
        vector = await _embed_text(content)
        if vector:
            payload = {
                "doc_id": f"shill:{digest[:20]}",
                "kind": "shill_evidence",
                "source": source or account or "telegram_shill",
                "token": str(token)[:80],
                "chain": str(chain)[:40],
                "url": x_url,
                "image_path": image_path,
                "ocr_text": ocr,
                "captured_at": datetime.now(UTC).isoformat(),
            }
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.put(
                    f"{_QDRANT_URL}/collections/{_SHILL_EVIDENCE_COLLECTION}/points",
                    params={"wait": True},
                    json={"points": [{"id": str(point_id), "vector": vector, "payload": payload}]},
                )
            if resp.status_code in (200, 202):
                return True
            logger.debug("shill_evidence:qdrant_status=%d body=%s", resp.status_code, resp.text[:160])
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_evidence:qdrant_failed err=%s", str(exc)[:160])
    return bool(db_saved)


# ══════════════════════════════════════════════════════════════════════════
# READ — Qdrant vector search (fail-open ``[]``)
# ══════════════════════════════════════════════════════════════════════════


async def search_evidence(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Semantic search over the shill evidence Qdrant collection.

    Embeds *query* and queries ``threat_intel_v2`` (payload ``kind`` =
    ``shill_evidence``). Fail-open: no embedder / Qdrant down → ``[]``.
    """
    vector = await _embed_text(query)
    if not vector:
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{_QDRANT_URL}/collections/{_SHILL_EVIDENCE_COLLECTION}/points/search",
                params={"limit": max(1, int(limit))},
                json={
                    "vector": vector,
                    "with_payload": True,
                    "filter": {"must": [{"key": "kind", "match": {"value": "shill_evidence"}}]},
                },
            )
        if resp.status_code != 200:
            logger.debug("shill_evidence:search_http=%d", resp.status_code)
            return []
        data = resp.json() or {}
        return [h.get("payload") or {} for h in data.get("result", []) if isinstance(h, dict)]
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        logger.debug("shill_evidence:search_failed err=%s", str(exc)[:120])
        return []


__all__ = [
    "list_shill_evidence",
    "read_shill_evidence",
    "search_evidence",
    "write_evidence",
]
