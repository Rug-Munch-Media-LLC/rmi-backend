"""Official RugMunch brand chat personas — one source for Telegram and the website.

Three public voices: ``desk`` (market and scanner numbers), ``security``
(exploits, scams, wallet risk), ``news`` (current events with citations). They
differ in what counts as best evidence and in when they must refuse — never in
whether they cite.

``PERSONAS`` holds a complete, paste-ready ``system_prompt`` per voice with the
shared guardrails composed in from ``_prompts``, so no caller can ship a voice
without them. ``select_persona`` is deterministic — command, then keyword score,
then surface default; ties resolve security > news > desk because routing a scam
question to the desk voice is the expensive mistake. ``build_messages`` numbers
every passage (``[E1]``, ``[E2]`` ...) because each claim needs a marker.

Stdlib-only on purpose: the bot, the website route and the tests import this
without booting ``app.config``, which demands production secrets.
"""

from __future__ import annotations

import re
from typing import Any, Final

from ._prompts import DESK_PROMPT, NEWS_PROMPT, SECURITY_PROMPT

MAX_WORDS_DEFAULT: Final = 180

# Mirrors app/domains/chat_tools/dispatcher.py::_EXECUTORS. Parity is asserted in
# tests/unit/test_chat_personas.py by AST-parsing that file (importing it needs Settings).
DATA_TOOLS: Final = (
    "price",
    "scan",
    "trace",
    "ta",
    "market",
    "news",
    "suspicious",
    "prediction",
    "perp",
)
TOOL_KEYS: Final = frozenset((*DATA_TOOLS, "rag", "web_search"))

SURFACES: Final[dict[str, str]] = {"telegram": "desk", "website": "desk"}
PRIORITY: Final = ("security", "news", "desk")
DEFAULT_PERSONA: Final = "desk"

PERSONAS: Final[dict[str, dict[str, Any]]] = {
    "desk": {
        "name": "RugMunch Desk",
        "tagline": "Numbers first: scanner verdicts, risk scores, market structure.",
        "system_prompt": DESK_PROMPT,
        "temperature": 0.3,
        "max_words": MAX_WORDS_DEFAULT,
        "tools_allowed": (
            "rag",
            "web_search",
            "price",
            "market",
            "ta",
            "scan",
            "suspicious",
            "prediction",
            "perp",
        ),
        "refusal_text": "I don't have evidence for that number. Missing: a retrieved price, scanner or TA passage for {asset}. Send the contract address or $TICKER and I'll pull the scanner verdict.",
    },
    "security": {
        "name": "RugMunch Security Desk",
        "tagline": "What the exploit does, who is exposed, and what the evidence supports.",
        "system_prompt": SECURITY_PROMPT,
        "temperature": 0.15,
        "max_words": MAX_WORDS_DEFAULT,
        "tools_allowed": ("rag", "web_search", "scan", "trace", "suspicious", "price"),
        "refusal_text": "I won't call that malicious without evidence. Missing: {missing}. Send the contract address or wallet and I'll run the scanner verdict.",
    },
    "news": {
        "name": "RugMunch News Desk",
        "tagline": "Current crypto events, every claim sourced and dated.",
        "system_prompt": NEWS_PROMPT,
        "temperature": 0.2,
        "max_words": MAX_WORDS_DEFAULT,
        "tools_allowed": ("rag", "web_search", "news"),
        "refusal_text": "Insufficient evidence. Nothing in the retrieved sources covers {event} after {date}. I can't confirm it happened.",
    },
}

_ROUTING_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "security": (
        "rug",
        "rugpull",
        "rug pull",
        "honeypot",
        "scam",
        "exploit",
        "drain",
        "drained",
        "phishing",
        "stolen",
        "hacked",
        "hack",
        "malicious",
        "approval",
        "revoke",
        "sweeper",
        "ponzi",
        "wallet risk",
    ),
    "news": (
        "news",
        "today",
        "latest",
        "announced",
        "breaking",
        "this week",
        "last week",
        "happened",
        "report",
    ),
    "desk": (
        "price",
        "chart",
        "liquidity",
        "holders",
        "mcap",
        "market cap",
        "volume",
        "rsi",
        "prediction",
        "funding",
    ),
}
_ROUTING_RES: Final[dict[str, re.Pattern[str]]] = {
    key: re.compile(r"\b(?:" + "|".join(re.escape(term) for term in terms) + r")\b", re.IGNORECASE)
    for key, terms in _ROUTING_TERMS.items()
}
_COMMAND_RE: Final = re.compile(r"^\s*/(desk|security|news)(?:@\w+)?\b", re.IGNORECASE)


def persona_for_surface(surface: str) -> str:
    """Default persona for *surface*; an unknown surface is a wiring bug."""
    try:
        return SURFACES[surface]
    except KeyError as exc:
        raise ValueError(f"unknown chat surface {surface!r}; expected one of {sorted(SURFACES)}") from exc


def select_persona(message: str, surface: str = "website") -> str:
    """Persona key for *message* on *surface*: command > keyword score > default."""
    default = persona_for_surface(surface)
    match = _COMMAND_RE.match(message or "")
    if match:
        return match.group(1).lower()
    scores = {key: len(rx.findall((message or "").lower())) for key, rx in _ROUTING_RES.items()}
    best = max(scores.values())
    if best:
        return next(key for key in PRIORITY if scores[key] == best)
    return default


def get_persona(persona_key: str) -> dict[str, Any]:
    """Return one persona entry, raising on an unknown key."""
    try:
        return PERSONAS[persona_key]
    except KeyError as exc:
        raise ValueError(f"unknown persona {persona_key!r}; expected one of {sorted(PERSONAS)}") from exc


def build_messages(
    persona_key: str,
    question: str,
    evidence: tuple[str, ...] = (),
    today: str | None = None,
) -> list[dict[str, str]]:
    """Build one turn: evidence numbered [E1], [E2] ..., *today* as a plain system line."""
    system = get_persona(persona_key)["system_prompt"]
    if today:
        system = f"{system}\n\nCurrent date: {today}."
    block = (
        "\n".join(f"[E{index}] {text}" for index, text in enumerate(evidence, start=1))
        if evidence
        else "(no evidence passages were retrieved)"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"EVIDENCE\n{block}\n\nQUESTION\n{question}"},
    ]


def public_personas() -> list[dict[str, str]]:
    """Persona-picker metadata for the website and Telegram menus — no prompt text."""
    return [{"key": key, "name": entry["name"], "tagline": entry["tagline"]} for key, entry in PERSONAS.items()]
