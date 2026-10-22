"""Rolling conversation context (last 3 turns) keyed by conversation_id.

Fail-open: any Redis problem returns an empty history rather than breaking
the answer. Only the conversation id is hashed for keying; message text is
never logged.
"""

from __future__ import annotations

import hashlib
import json

from app.core.logging import get_logger

log = get_logger(__name__)

MAX_TURNS = 3
MAX_ENTRIES = MAX_TURNS * 2
TTL_S = 86_400
_MAX_CHARS = 1200


def _key(conversation_id: str) -> str:
    digest = hashlib.sha256(conversation_id.encode("utf-8", "ignore")).hexdigest()[:24]
    return f"rmi:chat:ctx:{digest}"


class ConversationMemory:
    """Short rolling context store backed by a Redis list."""

    async def _client(self):
        try:
            from app.core.redis import get_redis_async

            return get_redis_async()
        except Exception as exc:
            log.warning("chat_memory_redis_unavailable err=%s", type(exc).__name__)
            return None

    async def history(self, conversation_id: str | None) -> list[dict[str, str]]:
        if not conversation_id:
            return []
        client = await self._client()
        if client is None:
            return []
        try:
            raw = await client.lrange(_key(conversation_id), -MAX_ENTRIES, -1)
            turns: list[dict[str, str]] = []
            for item in raw or []:
                try:
                    parsed = json.loads(item)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(parsed, dict) and parsed.get("role") and parsed.get("content"):
                    turns.append({"role": str(parsed["role"]), "content": str(parsed["content"])})
            return turns[-MAX_ENTRIES:]
        except Exception as exc:
            log.warning("chat_memory_read_failed err=%s", type(exc).__name__)
            return []

    async def append(
        self, conversation_id: str | None, question: str, answer: str
    ) -> None:
        if not conversation_id:
            return
        client = await self._client()
        if client is None:
            return
        key = _key(conversation_id)
        try:
            for role, text in (("user", question), ("assistant", answer)):
                payload = json.dumps({"role": role, "content": text[:_MAX_CHARS]})
                await client.rpush(key, payload)
            await client.ltrim(key, -MAX_ENTRIES, -1)
            await client.expire(key, TTL_S)
        except Exception as exc:
            log.warning("chat_memory_write_failed err=%s", type(exc).__name__)
