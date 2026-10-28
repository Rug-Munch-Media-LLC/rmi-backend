"""Transports for one rendered post — and the fake the dry run uses.

A sender is one method: ``send(chat_id, payload, lane=...) -> SentMessage``.
That is deliberately smaller than the headless client so tests, a Redis
queue or a future sender can all satisfy it without an adapter layer.

``SentMessage.message_id`` is ``None`` when the transport does not surface an
id (the headless wrapper returns a bool); the delivery log records what the
transport actually reported and never invents an id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.domains.telegram.editions.payloads import TelegramPayload


@dataclass(frozen=True, slots=True)
class SentMessage:
    """What a transport reports back. ``message_id`` may be unknown."""

    ok: bool
    message_id: int | None = None
    retry_after: float | None = None


class Sender(Protocol):
    """Minimal transport contract: one rendered post into one chat."""

    async def send(
        self, chat_id: int | str, payload: TelegramPayload, *, lane: str
    ) -> SentMessage: ...


class HeadlessSender:
    """Adapter over ``HeadlessTelegramClient`` (bool-returning send methods)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def send(
        self, chat_id: int | str, payload: TelegramPayload, *, lane: str
    ) -> SentMessage:
        if payload.needs_media:
            raw = await self._client.send_photo(chat_id, payload.image_url, caption=payload.text)
        else:
            raw = await self._client.send_message(
                chat_id,
                payload.text,
                parse_mode=payload.parse_mode,
                link_preview=not payload.disable_web_page_preview,
            )
        if isinstance(raw, bool):
            return SentMessage(ok=raw)
        message_id = getattr(raw, "id", None)
        return SentMessage(ok=message_id is not None or bool(raw), message_id=message_id)


class RecordingSender:
    """Dry-run sender: records what *would* be sent, never touches a network."""

    def __init__(self, message_id_start: int = 9000) -> None:
        self.sent: list[dict[str, Any]] = []
        self._next_id = message_id_start

    async def send(
        self, chat_id: int | str, payload: TelegramPayload, *, lane: str
    ) -> SentMessage:
        self._next_id += 1
        self.sent.append({"lane": lane, "chat_id": chat_id, **payload.as_dict()})
        return SentMessage(ok=True, message_id=self._next_id)


__all__ = ["HeadlessSender", "RecordingSender", "Sender", "SentMessage"]
