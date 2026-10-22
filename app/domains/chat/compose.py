"""Prompt assembly, answer validation, and the never-fail answer builders."""

from __future__ import annotations

import re

from app.domains.chat.retrieval import Passage
from app.domains.chat.schemas import Citation

MAX_WORDS = 180
MAX_HISTORY_ENTRIES = 6
_PASSAGE_CHARS = 700
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")

RULES = (
    "Rules: answer in at most 180 words. Use ONLY the numbered passages. "
    "Every paragraph must end with a citation marker like [1] or [2][3]. "
    "If the passages do not answer the question, say so in one sentence with "
    "the closest citation. Never invent numbers, URLs or protocol details."
)


def _passage_block(passages: list[Passage]) -> str:
    lines = []
    for idx, passage in enumerate(passages, start=1):
        link = f" ({passage.url})" if passage.url else ""
        lines.append(f"[{idx}] {passage.title}{link}\n{passage.text[:_PASSAGE_CHARS]}")
    return "\n\n".join(lines)


def build_messages(
    persona_prompt: str,
    passages: list[Passage],
    question: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Compose the chat messages: persona + numbered passages + question."""
    system = f"{persona_prompt}\n\n{RULES}"
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    messages.extend(history[-MAX_HISTORY_ENTRIES:])
    user = f"Passages:\n{_passage_block(passages)}\n\nQuestion: {question}"
    messages.append({"role": "user", "content": user})
    return messages


def stricter_nudge() -> dict[str, str]:
    return {
        "role": "system",
        "content": "Your previous draft was rejected. Under 180 words, and every "
        "paragraph must carry a [n] citation from the passages.",
    }


def _truncate_words(text: str, limit: int = MAX_WORDS) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    truncated = " ".join(words[:limit])
    for stop in (". ", "! ", "? "):
        pos = truncated.rfind(stop)
        if pos > len(truncated) // 2:
            return truncated[: pos + 1].strip()
    return truncated.rstrip(" ,;:") + "."


def _cited_indices(paragraph: str) -> list[int]:
    return [int(m) for m in _CITATION_RE.findall(paragraph)]


def validate_answer(text: str, passage_count: int) -> tuple[bool, str]:
    """Return ``(ok, reason)``. ``reason`` is empty when the draft passes."""
    body = (text or "").strip()
    if not body:
        return False, "empty"
    if len(body.split()) > MAX_WORDS:
        return False, "too_long"
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paragraphs:
        return False, "empty"
    for paragraph in paragraphs:
        indices = _cited_indices(paragraph)
        if not indices:
            return False, "missing_citation"
        if any(i < 1 or i > passage_count for i in indices):
            return False, "bad_citation_index"
    return True, ""


def to_citations(passages: list[Passage]) -> list[Citation]:
    return [
        Citation(
            index=idx,
            title=passage.title,
            url=passage.url,
            library_id=passage.library_id,
            source=passage.source,
            score=round(passage.score, 4),
        )
        for idx, passage in enumerate(passages, start=1)
    ]


def retrieval_only(passages: list[Passage], reason: str) -> tuple[str, list[Citation]]:
    """Top two passages verbatim + links — the model-free answer."""
    top = passages[:2]
    if not top:
        return (
            "I could not find enough grounded evidence to answer that.",
            [],
        )
    blocks = []
    for idx, passage in enumerate(top, start=1):
        link = f" — {passage.url}" if passage.url else ""
        blocks.append(f"{passage.title}{link}\n{passage.text[:400]} [{idx}]")
    header = "Grounded excerpts (live model unavailable):"
    answer = header + "\n\n" + "\n\n".join(blocks)
    return _truncate_words(answer, MAX_WORDS + 60), to_citations(top)


def refusal() -> tuple[str, list[Citation]]:
    """Answer + empty citations for the insufficient-evidence case."""
    text = (
        "I could not find enough grounded evidence in the knowledge base or on "
        "the web to answer that. Try naming the token, protocol or date, or ask "
        "a narrower question."
    )
    return text, []
