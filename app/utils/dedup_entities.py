"""Level 4: crypto-entity extraction, overlap matching and the time window.

Entities are ``$TICKER`` symbols, known tokens/protocols/people, and EVM or
Solana addresses. Overlap coefficient ``|A∩B| / min(|A|,|B|)`` above the
threshold — on two articles published within six hours — means "same story".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.utils.dedup_config import ENTITY_OVERLAP_THRESHOLD, ENTITY_WINDOW_HOURS
from app.utils.dedup_vocabulary import KNOWN_PEOPLE, KNOWN_PROTOCOLS, KNOWN_TOKENS

_TOKEN_RE = re.compile(r"\$([a-zA-Z]{2,12})\b")
_ADDRESS_EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_ADDRESS_SOL_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def extract_crypto_entities(text: str) -> set[str]:
    """Extract a normalized set of crypto entities from *text*.

    Entities: ``$TICKER`` symbols, known token symbols, known protocols,
    known people, EVM addresses, and Solana addresses. Everything is
    lowercased so comparisons are case-insensitive. Returns an empty set
    when nothing recognizable is found (callers treat that as "can't
    decide" → fail-open).
    """
    if not text:
        return set()
    lowered = text.lower()
    entities: set[str] = set()

    for m in _TOKEN_RE.finditer(text):
        entities.add(m.group(1).lower())

    for vocab in (KNOWN_TOKENS, KNOWN_PROTOCOLS, KNOWN_PEOPLE):
        for term in vocab:
            if re.search(rf"\b{re.escape(term)}\b", lowered):
                entities.add(term)

    entities.update(_ADDRESS_EVM_RE.findall(text.lower()))
    entities.update(_ADDRESS_SOL_RE.findall(text))

    return entities


def is_duplicate_by_entities(text_a: str, text_b: str, threshold: float = ENTITY_OVERLAP_THRESHOLD) -> bool:
    """True when *text_a* and *text_b* share >80% of crypto entities.

    Overlap coefficient = |A ∩ B| / min(|A|, |B|). When either side has no
    entities we cannot decide → False (fail-open).
    """
    ent_a = extract_crypto_entities(text_a)
    ent_b = extract_crypto_entities(text_b)
    if not ent_a or not ent_b:
        return False
    return len(ent_a & ent_b) / min(len(ent_a), len(ent_b)) >= threshold


def parse_published_at(value: str) -> datetime | None:
    """Parse a published_at string (ISO or RSS) to an aware UTC datetime."""
    if not value:
        return None
    v = value.strip()
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        dt = None
    if dt is None:
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(v)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def within_window(ts_a: datetime | None, ts_b: datetime | None, hours: int = ENTITY_WINDOW_HOURS) -> bool:
    """True when both timestamps exist and are within *hours* of each other."""
    if ts_a is None or ts_b is None:
        return False
    return abs((ts_a - ts_b).total_seconds()) <= hours * 3600
