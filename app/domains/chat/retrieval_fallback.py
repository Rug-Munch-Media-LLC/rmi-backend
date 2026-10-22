"""Deterministic last-resort answer built from retrieved evidence only."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

HEADER = (
    "The live models are unavailable right now, so here is what our own "
    "retrieval index returns for your question:"
)
NO_EVIDENCE = (
    "No matching sources were retrieved for this question. Please retry in a "
    "few minutes — nothing was answered from model knowledge."
)
MAX_SOURCES = 5


def retrieval_only_answer(retrieval: Sequence[dict] | None) -> str:
    """Render citations without ever calling a model.

    Contract for each retrieval item: ``{"text": str, "source": str,
    "url": str | None}``. Output is deterministic — same input, same string.
    """
    items = [item for item in (retrieval or []) if str(item.get("text", "")).strip()]
    if not items:
        return f"{HEADER}\n\n{NO_EVIDENCE}"

    lines = [HEADER]
    for index, item in enumerate(items[:MAX_SOURCES], start=1):
        body = re.sub(r"\s+", " ", str(item["text"])).strip()[:600]
        source = str(item.get("source") or "internal corpus").strip()
        url = str(item.get("url") or "").strip()
        citation = f"[{index}] {source}"
        if url:
            citation = f"{citation} ({url})"
        lines.append(f"{citation}\n{body}")
    lines.append("Sources are listed above; no model-generated text was used.")
    return "\n\n".join(lines)
