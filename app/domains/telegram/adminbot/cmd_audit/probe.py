"""Minimal Update/Context stubs so read-only handlers run in-process."""

from __future__ import annotations

from typing import Any


class _Message:
    def __init__(self, sink: list[str], text: str = "") -> None:
        self._sink = sink
        self.text = text

    async def reply_text(self, *args: Any, **kwargs: Any) -> Any:
        self._sink.append(str(args[0])[:120] if args else "")
        return self

    async def reply_markdown_v2(self, *args: Any, **kwargs: Any) -> Any:
        return await self.reply_text(*args, **kwargs)

    async def edit_text(self, *args: Any, **kwargs: Any) -> Any:
        return await self.reply_text(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> bool:
        return True


class _Chat:
    id = 1
    type = "private"


def _owner_id() -> int:
    """A real owner id, so handlers exercise their body instead of the gate."""
    try:
        from app.domains.telegram.adminbot.config import ADMIN_CHAT_ID, OWNER_IDS

        return min(OWNER_IDS) if OWNER_IDS else ADMIN_CHAT_ID
    except Exception:
        return 0


class _User:
    username = "diag"
    first_name = "diag"
    is_bot = False

    def __init__(self) -> None:
        self.id = _owner_id()


class _Bot:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    async def send_message(self, *args: Any, **kwargs: Any) -> _Message:
        text = kwargs.get("text") or (args[1] if len(args) > 1 else "")
        self._sink.append(str(text)[:120])
        return _Message(self._sink)

    async def send_chat_action(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def get_me(self) -> Any:
        class _Me:
            username = "diag"

        return _Me()


class _Update:
    def __init__(self, sink: list[str], text: str = "") -> None:
        self.message = _Message(sink, text)
        self.effective_message = self.message
        self.effective_chat = _Chat()
        self.effective_user = _User()
        self.callback_query = None
        self.update_id = 1


class _Context:
    def __init__(self, sink: list[str], args: list[str]) -> None:
        self.bot = _Bot(sink)
        self.args = args
        self.user_data: dict[str, Any] = {}
        self.chat_data: dict[str, Any] = {}
        self.bot_data: dict[str, Any] = {}
        self.job_queue = None


def probe_pair(sink: list[str], args: list[str]) -> tuple[Any, Any]:
    """Build a (Update, Context) pair that only records what handlers reply."""
    return _Update(sink), _Context(sink, args)
