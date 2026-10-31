"""Idempotency ledger for lane deliveries.

The unit of idempotency is ``(day, lane, slug)``: the same story is never
posted twice into the same lane on the same UTC day, and re-running a plan
(operator retry, a scheduler that fired twice, a crash between send and
record) is a no-op instead of a duplicate post.

The interface is a protocol so a Redis-backed ledger can replace the
in-memory one without touching routing or delivery code. The in-memory
implementation is the default: single-writer per process, which is what the
edition scheduler is.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable


def day_key(moment: datetime | None = None) -> str:
    """UTC day bucket, ``YYYY-MM-DD``."""
    return (moment or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d")


def slug_key(slug: str, fallback: str = "") -> str:
    """Stable story key: the slug itself, or a short hash when it is missing."""
    cleaned = str(slug or "").strip().lower()
    if cleaned:
        return cleaned
    digest = hashlib.sha256(str(fallback).encode()).hexdigest()[:16]
    return f"anon-{digest}"


def dedupe_key(day: str, lane: str, slug: str, fallback: str = "") -> str:
    """``day:lane:slug`` — the exact key routing and delivery agree on."""
    return f"{day}:{lane}:{slug_key(slug, fallback)}"


@dataclass(frozen=True, slots=True)
class LedgerRow:
    key: str
    day: str
    lane: str
    slug: str
    message_id: int | None = None


class DeliveryLedger(Protocol):
    """What routing and delivery need from an idempotency store."""

    def already_sent(self, key: str) -> bool: ...

    def lane_count(self, day: str, lane: str) -> int: ...

    def record(self, key: str, *, day: str, lane: str, slug: str, message_id: int | None = None) -> None: ...


class InMemoryLedger:
    """Process-local ledger. Swap for Redis when several processes publish."""

    def __init__(self) -> None:
        self._rows: dict[str, LedgerRow] = {}

    def already_sent(self, key: str) -> bool:
        return key in self._rows

    def lane_count(self, day: str, lane: str) -> int:
        return sum(1 for row in self._rows.values() if row.day == day and row.lane == lane)

    def record(
        self,
        key: str,
        *,
        day: str,
        lane: str,
        slug: str,
        message_id: int | None = None,
    ) -> None:
        self._rows[key] = LedgerRow(key=key, day=day, lane=lane, slug=slug, message_id=message_id)

    def rows(self, day: str | None = None) -> tuple[LedgerRow, ...]:
        return tuple(row for row in self._rows.values() if day is None or row.day == day)

    def reset(self) -> None:
        self._rows.clear()

    def extend(self, rows: Iterable[LedgerRow]) -> None:
        for row in rows:
            self._rows[row.key] = row


__all__ = [
    "DeliveryLedger",
    "InMemoryLedger",
    "LedgerRow",
    "day_key",
    "dedupe_key",
    "slug_key",
]
