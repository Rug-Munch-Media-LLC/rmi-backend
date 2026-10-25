"""Shared doubles for the dedup pipeline tests (no monkeypatching)."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.utils.dedup import bind_title_search, reset_title_search

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class Hit:
    """Stand-in for the engine search hit."""

    doc_id: str
    score: float
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def make_search(hits: list[Hit], calls: list[tuple[str, str, int]] | None = None) -> Any:
    """Async title search returning *hits*; records its args when *calls* is given."""

    async def search(title: str, collection: str, limit: int) -> list[Hit]:
        if calls is not None:
            calls.append((title, collection, limit))
        return hits

    return search


async def failing_search(*_args: Any, **_kwargs: Any) -> list[Hit]:
    """Title search that raises, for the fail-open path."""
    raise RuntimeError("qdrant unreachable")


@contextmanager
def use_search(fn: Any) -> Iterator[Any]:
    """Bind *fn* as the title search for the block, then reset the token."""
    token = bind_title_search(fn)
    try:
        yield fn
    finally:
        reset_title_search(token)
