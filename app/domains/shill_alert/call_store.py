"""Append-only JSON-file store for captured scammer-call posts.

Bridges the social-intel ingestion pipeline and the read-only
``GET /api/v1/kol/scammer-calls`` endpoint. Each ``add`` appends one JSON
object per line (JSON Lines) to ``app/databus/scammer_calls.json`` — the
file is never rewritten, so concurrent writers never clobber each other and
history is preserved. Reads are newest-first and never raise: a missing or
corrupt file degrades to an empty list.

Call record contract (the shape the frontend expects on
``GET /api/v1/kol/scammer-calls``):

    {
        "id": "unique-call-id",
        "handle": "handle-or-channel",
        "channel": "telegram channel or null",
        "post_text": "original post text",
        "token_symbol": "PEPE",
        "token_address": "0x... or null",
        "chain": "ethereum | solana | ...",
        "severity": "critical | high | medium | low",
        "captured_at": "ISO-8601 timestamp",
        "source_url": "https://...",
    }

Pure file layer — no network, no DB, no FastAPI.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "databus" / "scammer_calls.json"


class CallStore:
    """Append-only JSON Lines store of scammer-call records."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _DEFAULT_PATH
        self._lock = threading.Lock()

    def add(self, call: dict[str, Any]) -> bool:
        """Append *call* as one JSON line; True on success, False on I/O error.

        The parent directory is created on demand. Records are stored
        verbatim (any dict keys are preserved).
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(call, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self._lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            return True
        except OSError as e:  # fail-open: pipeline must not crash on disk issues
            logger.warning("call_store_add_failed: %s", str(e)[:120])
            return False

    def list(self, limit: int = 100, severity: str | None = None) -> list[dict[str, Any]]:
        """Read calls, newest first, optionally filtered by severity.

        Severity matching is case-insensitive. Corrupt lines are skipped and
        a missing file yields ``[]``. ``limit`` caps the most recent N
        records (0/negative → no cap).
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        calls: list[dict[str, Any]] = []
        sev = str(severity or "").strip().lower()
        for line_no, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError as e:
                logger.warning("call_store_corrupt_line_%d: %s", line_no, str(e)[:80])
                continue
            if not isinstance(record, dict):
                continue
            if sev and str(record.get("severity") or "").strip().lower() != sev:
                continue
            calls.append(record)
        if limit and limit > 0:
            calls = calls[-int(limit) :]
        return list(reversed(calls))

    def to_dict(self, limit: int = 100, severity: str | None = None) -> dict[str, Any]:
        """The ``{calls, total}`` payload the endpoint returns (empty-safe)."""
        calls = self.list(limit=limit, severity=severity)
        return {"calls": calls, "total": len(calls)}
