"""Chat message persistence (SQLite) for the chat endpoint.

Split out of ``app/routers/chat/__init__.py`` (de-godding phase 2,
behavior-preserving). Re-exported from the package root so existing
importers keep working. The DB connection helpers live in
``app/routers/chat_db.py`` (shared with the history/stats sub-router).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..chat_db import _get_db


def _save_message(user_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> Any:
    msg_id = f"msg-{uuid.uuid4().hex[:10]}"
    conn = _get_db()
    conn.execute(
        "INSERT INTO chat_messages (id, user_id, role, content, metadata) VALUES (?, ?, ?, ?, ?)",
        (msg_id, user_id, role, content, json.dumps(metadata or {})),
    )
    conn.commit()
    conn.close()
    return msg_id
