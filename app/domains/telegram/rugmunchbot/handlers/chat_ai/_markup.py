"""Markdown/HTML formatters + text chunking for the free-text AI chat pipeline.

Extracted from ``chat_ai/_helpers.py`` during the de-godding pass: the v10
response beautifier (emoji section headers, thin separators, ``**bold**``
markers), the raw-HTML → markdown normalization (defense-in-depth before
``html.escape()``), the paragraph/sentence-aware chunker, the scanner-context
summary line, and the markdown table → bullet-list fallback. Pure functions —
no ``Update``/``Context`` state, no handler registration. ``_helpers.py``
re-imports everything it needs from here; behavior is byte-for-byte identical.
"""

from __future__ import annotations

import re
from typing import Any

from app.domains.telegram.rugmunchbot.formatters import thin_sep
from app.domains.telegram.rugmunchbot.handlers.chat_ai._parts import _is_fence_line

# Telegram hard limit is 4096; leave headroom for escape expansion.
CHUNK_MAX_CHARS = 3800

# ══════════════════════════════════════════════
# v10 RESPONSE BEAUTIFIER (see _beautify_answer / _markdown_bold_to_html)
# Section emojis match the persona's RESPONSE FORMATTING block (prompts.py).
# A line that STARTS with one of these and is short is treated as a section
# header: normalized to **emoji Title**, with thin_sep() inserted beneath it.
# ══════════════════════════════════════════════
_SECTION_EMOJIS: frozenset[str] = frozenset(
    [
        "\U0001f4ca",  # 📊 statistics
        "\U0001f4c8",  # 📈 chart increasing
        "\U0001f4c9",  # 📉 chart decreasing
        "\U0001f50d",  # 🔍 magnifying glass
        "\U0001f6e1\ufe0f",  # 🛡️ shield
        "\U0001f40b",  # 🐋 whale
        "\U0001f464",  # 👤 bust
        "\U0001f4b0",  # 💰 money bag
        "\U0001f525",  # 🔥 fire
        "\U0001f6a8",  # 🚨 siren
        "\u2705",  # ✅ check
        "\u26a0\ufe0f",  # ⚠️ warning
        "\U0001f4ac",  # 💬 speech balloon
        "\U0001f4f0",  # 📰 newspaper
        "\U0001f465",  # 👥 people
        "\U0001f680",  # 🚀 rocket
    ]
)
_SECTION_HEADER_MAX = 80  # longer emoji-prefixed lines are content, not headers
_BOLD_RE = re.compile(r"\*{2,3}([^*\n]+?)\*{2,3}")

# ══════════════════════════════════════════════
# RAW HTML → MARKDOWN NORMALIZATION (defense-in-depth)
# Local models sometimes emit RAW HTML (<b>BTC:</b>, &amp;, &lt;code&gt;)
# instead of Telegram markdown — even when the system prompts forbid it.
# Normalize it to markdown BEFORE escaping so the later escape() +
# _markdown_bold_to_html turn it into real, balanced HTML instead of
# literal garbage (or unbalanced tags that Telegram rejects).
# ══════════════════════════════════════════════
_HTML_ENTITY_RE = re.compile(r"&(?:lt|gt|amp|quot|#x27);")
_HTML_ENTITY_MAP: dict[str, str] = {
    "&lt;": "<",
    "&gt;": ">",
    "&amp;": "&",
    "&quot;": '"',
    "&#x27;": "'",
}
_RAW_HTML_TAG_RE = re.compile(r"</?(?:b|strong|i|em|code|pre)\b[^>]*>")
_RAW_HTML_TAG_TO_MARKDOWN: dict[str, str] = {
    "b": "**",
    "strong": "**",
    "i": "_",
    "em": "_",
    "code": "`",
    "pre": "`",
}


def _unescape_llm_entities(text: str) -> str:
    """Unescape HTML entities a model emitted when it "thought" in HTML.

    Single-pass so double-encoded entities degrade safely (``&amp;lt;``
    becomes ``&lt;``, never a second unescape round).
    """
    return _HTML_ENTITY_RE.sub(lambda m: _HTML_ENTITY_MAP[m.group(0)], text)


def _raw_html_emphasis_to_markdown(text: str) -> str:
    """Convert raw HTML emphasis tags to Telegram markdown markers.

    ``<b>``/``<strong>`` → ``**``, ``<i>``/``<em>`` → ``_``,
    ``<code>``/``<pre>`` → backticks. Tags outside the emphasis set
    (``<br>``, ``<img>``, …) are left untouched.
    """

    def _repl(m: re.Match[str]) -> str:
        tag = m.group(0).strip("<>/").split()[0].lower()
        return _RAW_HTML_TAG_TO_MARKDOWN.get(tag, m.group(0))

    return _RAW_HTML_TAG_RE.sub(_repl, text)


def _normalize_llm_markup(text: str) -> str:
    """Defense-in-depth: normalize raw HTML an LLM emitted into markdown.

    Runs on the RAW answer BEFORE ``html.escape()``: unescapes entities the
    model already escaped (``&amp;`` → ``&``), then converts raw emphasis
    tags to markdown (``<b>BTC:</b>`` → ``**BTC:**``). The subsequent escape
    + :func:`_markdown_bold_to_html` then produce real, balanced HTML.

    Fenced code blocks are NOT special-cased: a literal ``<b>`` inside a
    code block is converted too. This is a best-effort heuristic for a rare
    model failure mode — the system prompts forbid raw HTML outright.
    """
    return _raw_html_emphasis_to_markdown(_unescape_llm_entities(text))


# ══════════════════════════════════════════════
# SCANNER CONTEXT (address questions get a risk summary prepended)
# ══════════════════════════════════════════════


def _format_scan_summary(address: str, data: dict[str, Any]) -> str | None:
    """Compact single-line risk summary used to ground an address question.

    Returns ``None`` when the scanner returned no risk score so the caller
    falls through to a plain question.
    """
    risk = data.get("risk_score")
    if risk is None:
        return None
    flags = [f"risk_score={int(risk)}"]
    for key in ("is_honeypot", "is_mintable", "is_renounced"):
        val = data.get(key)
        if val is not None:
            flags.append(f"{key}={bool(val)}")
    liq = data.get("liquidity_usd") or 0
    if liq:
        flags.append(f"liquidity=${float(liq):,.0f}")
    holders = data.get("holders")
    if holders:
        flags.append(f"holders={holders}")
    return f"Context: token {address} scan shows {', '.join(flags)}"


def _last_sentence_boundary(text: str, limit: int) -> int:
    """Index just past the last sentence-ending punctuation before ``limit``.

    Returns -1 when no sentence boundary exists inside the window. Used by
    :func:`_split_oversized` so hard splits land at sentence ends instead of
    mid-sentence ("split at natural section boundaries, not mid-sentence").
    """
    best = -1
    for sep_ in (". ", "? ", "! "):
        idx = text.rfind(sep_, 0, limit)
        if idx >= 0:
            best = max(best, idx + len(sep_))
    if best > limit:
        return limit  # boundary punctuation ends exactly at the window edge
    return best


def _split_oversized(paragraph: str, max_len: int) -> list[str]:
    """Hard-split a single oversized paragraph — prefer sentence boundaries.

    Falls back to the last word boundary, then an exact cut, so a cut never
    lands mid-word. Sentence-boundary preference keeps chunks readable.
    """
    if len(paragraph) <= max_len:
        return [paragraph]
    parts: list[str] = []
    remaining = paragraph
    while len(remaining) > max_len:
        cut = _last_sentence_boundary(remaining, max_len)
        if cut < max_len // 2:
            cut = remaining.rfind(" ", 0, max_len)  # no sentence end — word boundary
        if cut < max_len // 2:
            cut = max_len  # no good boundary — cut exactly
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _chunk_text(text: str, max_len: int = CHUNK_MAX_CHARS) -> list[str]:
    """Split ``text`` into Telegram-sized chunks on paragraph boundaries.

    Operates on the RAW (pre-escape) text: paragraphs are the safest split
    points and no chunk ever cuts through an HTML entity that escaping would
    later introduce (e.g. ``&lt;``). New emoji section headers (v10) always
    start a fresh chunk when the current one is non-empty, so headers never
    dangle at the tail of a previous section.
    """
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        for piece in _split_oversized(paragraph, max_len):
            if current and _is_section_header(piece):
                # v10: prefer section boundaries — a new emoji header leads a
                # new chunk so it is never glued to the previous section.
                chunks.append(current)
                current = ""
            elif current and len(current) + len(piece) + 2 > max_len:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


# ══════════════════════════════════════════════
# v10 RESPONSE BEAUTIFIER
# Runs on the RAW LLM answer BEFORE chunking/escaping: normalizes emoji
# section headers, inserts thin separators under them, and keeps markdown
# **bold** markers for the post-escape conversion in _send_answer.
# ══════════════════════════════════════════════


def _is_section_header(line: str) -> bool:
    """True for a short line that begins with a section emoji (v10 header).

    A line starting with an emoji but longer than ``_SECTION_HEADER_MAX`` is
    prose, not a header — e.g. "📊 This token's chart broke out of a wedge
    that's been building since March" stays content. Prefix matching handles
    multi-code-point emojis (🛡️ = U+1F6E1 + VS16).
    """
    core = line.strip().lstrip("*").strip()
    if not core or len(core) > _SECTION_HEADER_MAX:
        return False
    return any(core.startswith(emoji) for emoji in _SECTION_EMOJIS)


def _unwrap_bold(line: str) -> str:
    """Strip a balanced ``**...**``/``***...***`` wrapper (or leading `*`)."""
    stripped = line.strip()
    m = re.match(r"^\*{2,3}(.+?)\*{2,3}$", stripped)
    if m:
        return m.group(1).strip()
    return re.sub(r"^\*{1,3}", "", stripped).strip()


# Separator lines the model sometimes emits under a header (──── / ━━━━).
# Deduped against the thin_sep() the beautifier already inserts.
_SEPARATOR_LINE_RE = re.compile(r"^[\u2500\u2501\u2550\u2551\-_]{3,}$")
# Base emoji code points (excludes ZWJ so compound/role emoji are untouched);
# an optional VS16 is folded into the collapsed unit so 🛡️🛡️ → 🛡️ correctly.
_EMOJI_BASE = "\U0001f000-\U0001faff\u2600-\u27bf"
_LEADING_EMOJI_RUN_RE = re.compile(f"^([{_EMOJI_BASE}]\ufe0f?)(?:\\s*\\1)+")
# ALL-CAPS abbreviations that must survive title-casing (TA, RSI, MACD...).
_HEADER_ABBR = frozenset(
    {
        "TA",
        "RSI",
        "MACD",
        "EMA",
        "USD",
        "BTC",
        "ETH",
        "SOL",
        "BNB",
        "S/R",
        "DYOR",
        "AEGIS",
        "TVL",
        "ATH",
        "ATL",
        "HHI",
        "LP",
        "CEX",
        "DEX",
        "MEV",
        "AI",
        "NFT",
        "KOL",
        "P&L",
        "P/L",
        "GM",
        "GN",
        "X2",
    }
)
# Connector words in ALL-CAPS headers render lowercase ("TA and RSI").
_HEADER_CONNECTORS = frozenset({"AND", "OF", "THE", "FOR", "ON", "IN", "TO", "VS", "WITH"})


def _is_separator_line(line: str) -> bool:
    """True for a line that is only box-drawing dashes (model-emitted sep)."""
    return bool(_SEPARATOR_LINE_RE.match(line.strip()))


def _collapse_leading_emoji(text: str) -> str:
    """Collapse a run of identical emojis at the start of a header.

    ``📊📊📊 Market Read`` / ``📊 📊 Market Read`` → ``📊 Market Read``.
    The optional variation selector (VS16) is part of the collapsed unit so
    compound emoji like 🛡️ (U+1F6E1 + VS16) dedupe to 🛡️ — never 🛡.
    """
    return _LEADING_EMOJI_RUN_RE.sub(lambda m: m.group(1), text)


def _normalize_header_casing(text: str) -> str:
    """Best-effort header casing (v12): first word capitalized, ALL-CAPS
    prose title-cased, real abbreviations (TA/RSI/MACD/USD...) preserved.

    ``📊 market read`` → ``📊 Market read``; ``📊 MARKET READ`` →
    ``📊 Market Read``; ``📊 TA AND RSI`` → ``📊 TA and RSI``. Mixed-case
    headers are never rewritten, so proper nouns and $SYMBOLS survive.
    """
    words = text.split()
    if not words:
        return text
    # Leading pure-emoji tokens (📊 / 🛡️ / …) are not words — casing applies
    # to the first real word, and the emoji prefix is preserved verbatim.
    lead: list[str] = []
    while words and not any(ch.isalnum() for ch in words[0]):
        lead.append(words.pop(0))
    if not words:
        return text
    head = words[0]
    if head.islower() and head[0].isalpha():
        words[0] = head[0].upper() + head[1:]
    stripped = "".join(ch for ch in " ".join(words) if ch.isalpha())
    if len(stripped) >= 4 and stripped.isupper():
        words = [
            w.lower()
            if w in _HEADER_CONNECTORS
            else w
            if w in _HEADER_ABBR
            else (w[0].upper() + w[1:].lower())
            if w.isalpha()
            else w
            for w in words
        ]
    return " ".join(lead + words)


def _beautify_answer(answer: str) -> str:
    """v10 beautifier — normalize headers + insert thin separators (raw text).

    - Lines that look like section headers (start with a section emoji,
      short) are normalized to ``**emoji Title**`` on their own line, with a
      blank line before them so the chunker sees a clean section boundary.
    - ``thin_sep()`` is inserted beneath every header for visual rhythm
      (📊 header / ──── / content, like the /scan report blocks).
    - Model-emitted separator lines under a header are deduped (never two
      ``────`` in a row), repeated leading emojis collapse (no emoji spam),
      and header casing is normalized conservatively (v12).
    - All other lines are preserved verbatim (code blocks, lists, spacing).
    Markdown ``**bold**`` is left in place for :func:`_markdown_bold_to_html`
    which runs AFTER escaping in :func:`_send_answer`.
    """
    lines = answer.split("\n")
    out: list[str] = []
    prev_empty = True
    for raw_line in lines:
        if not raw_line.strip():
            out.append("")
            prev_empty = True
            continue
        if _is_section_header(raw_line):
            if not prev_empty:
                out.append("")
            header_text = _collapse_leading_emoji(_unwrap_bold(raw_line))
            header_text = _normalize_header_casing(header_text)
            out.append(f"**{header_text}**")
            out.append(thin_sep())
            prev_empty = False
            continue
        if _is_separator_line(raw_line):
            # The model put a separator under a header we already decorated —
            # skip it instead of stacking a second dashed line.
            if out and _is_separator_line(out[-1]):
                continue
            out.append(raw_line)
            prev_empty = False
            continue
        out.append(raw_line)
        prev_empty = False
    start = 0
    while start < len(out) and not out[start]:
        start += 1
    out = out[start:]
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _markdown_bold_to_html(text: str) -> str:
    """Convert ``**bold**`` / ``***bold***`` to ``<b>bold</b>``.

    MUST run AFTER ``html.escape()`` so the wrapped content is already
    entity-safe and only the asterisk pairs are replaced — LLM-supplied HTML
    is never re-introduced. Unbalanced asterisks pass through untouched.
    """
    return _BOLD_RE.sub(r"<b>\1</b>", text)


# ══════════════════════════════════════════════
# MARKDOWN TABLE → LIST FALLBACK
# Telegram HTML has no table element; a raw markdown pipe table would render
# as a wall of literal ``|`` characters. Convert table blocks to compact
# bullet lists BEFORE escaping. Fenced code blocks are never touched.
# ══════════════════════════════════════════════
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def _split_table_row(row: str) -> list[str]:
    """Split a markdown table row into trimmed cells (drop outer pipes)."""
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _is_table_block(rows: list[str]) -> bool:
    """True when consecutive pipe rows look like a markdown table.

    A separator row (``|---|``) is the definitive signal; a run of 3+ pipe
    rows without one (some LLMs skip the separator) is treated as a table
    too. Single stray pipe lines are left alone.
    """
    if len(rows) < 2:
        return False
    if any(_TABLE_SEP_RE.match(r) for r in rows):
        return True
    return len(rows) >= 3


def _render_table_block(rows: list[str]) -> list[str]:
    """Render a markdown table block as compact bullet lines (raw text).

    The header row (first row, when a separator row is present) becomes a
    ``**a · b**`` line so the bold conversion renders it as a header; each
    data row becomes ``• a · b · c``. The ``|---|`` separator row is
    dropped. ``·`` (U+00B7) keeps rows on one line without the pipe-wall
    look of the original table.
    """
    has_separator = len(rows) >= 2 and bool(_TABLE_SEP_RE.match(rows[1]))
    rendered: list[str] = []
    for idx, row in enumerate(rows):
        if _TABLE_SEP_RE.match(row):
            continue
        cells = _split_table_row(row)
        if not cells or any(not c for c in cells):
            continue
        if has_separator and idx == 0:
            rendered.append(f"**{' · '.join(cells)}**")
        else:
            rendered.append(f"\u2022 {' · '.join(cells)}")
    return rendered


def _tables_to_lists(text: str) -> str:
    """Convert markdown table blocks to compact bullet lists (raw text).

    Detect table blocks (consecutive ``|`` rows, with or without a
    ``|---|`` separator row) and render each row as ``• a · b · c`` with
    the header bolded. Fenced code blocks are never touched: pipe-shaped
    lines inside ````` ``` ```` stay verbatim.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _is_fence_line(line):
            in_fence = not in_fence
        if in_fence or not _TABLE_ROW_RE.match(line):
            out.append(line)
            i += 1
            continue
        run: list[str] = []
        while i < n and _TABLE_ROW_RE.match(lines[i]):
            run.append(lines[i])
            i += 1
        if not _is_table_block(run):
            out.extend(run)
            continue
        out.extend(_render_table_block(run))
    return "\n".join(out)


__all__ = [
    "CHUNK_MAX_CHARS",
    "_beautify_answer",
    "_chunk_text",
    "_format_scan_summary",
    "_is_section_header",
    "_markdown_bold_to_html",
    "_normalize_llm_markup",
    "_split_oversized",
    "_tables_to_lists",
    "_unescape_llm_entities",
]
