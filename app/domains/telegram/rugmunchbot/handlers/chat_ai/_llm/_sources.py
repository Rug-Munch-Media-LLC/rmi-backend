"""LLM clients — untrusted-context delimiters + source dedupe/rank/render.

All retrieved/external context is UNTRUSTED DATA: news articles / exploit
PoCs can embed instructions. Each block is wrapped in explicit
``<untrusted_context>`` delimiters; sources are de-duplicated by
normalized domain, ranked primary-first and rendered as plain text
``title — url`` so no markdown link ever leaks through the delivery
pipeline.
"""

from __future__ import annotations

import re


# ── Untrusted-context delimiters (indirect prompt-injection hardening) ─────
# All retrieved/external context — RAG hits, bridge market blocks, scan
# summaries, live tool data, real-time digests — is UNTRUSTED DATA: news
# articles / exploit PoCs can embed instructions that try to steer the model
# or the user. Each block is wrapped in explicit delimiters so the system
# prompts can tell the model to treat it strictly as reference data.
def _untrusted_block(block: str) -> str:
    return f"\n<untrusted_context>\n{block}\n</untrusted_context>\n"


# ── Source quality: dedupe + rank + plain-text citation rendering ──────────
# Primary / high-signal domains surface FIRST in the "Sources:" block —
# official docs, block explorers, major outlets and data aggregators beat
# random blogs and aggregator mirrors. Matching is substring-based on the
# normalized host so subdomains (docs.ethereum.org, explorer.solana.com)
# rank too.
_PRIMARY_SOURCE_MARKERS = (
    # block explorers / chain-native explorers
    "etherscan",
    "bscscan",
    "polygonscan",
    "basescan",
    "arbiscan",
    "solscan",
    "solana.fm",
    "explorer.",
    # official project docs / standards
    "ethereum.org",
    "bitcoin.org",
    "docs.",
    "github.com",
    # major crypto/media outlets + wire services
    "coindesk",
    "cointelegraph",
    "theblock",
    "decrypt",
    "blockworks",
    "reuters",
    "bloomberg",
    "forbes",
    "ft.com",
    "wsj",
    "apnews",
    "theguardian",
    "nytimes",
    # data aggregators / analytics
    "defillama",
    "coingecko",
    "coinmarketcap",
    "dexscreener",
    "messari",
)
# Markdown link syntax stripped from untrusted source titles / model answers
# so the final Telegram message can never show a literal ``[title](url)``
# leak (the delivery pipeline has no link pass). The inner text is kept.
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def _normalize_source_domain(url: str) -> str:
    """Normalize a source URL to its bare domain (dedupe key).

    ``https://www.etherscan.io/token/0x…?a=1`` → ``etherscan.io``. Empty when
    the source carries no URL (it renders as a plain title, never deduped).
    """
    u = url.strip()
    if not u:
        return ""
    for prefix in ("https://", "http://", "//"):
        if u.startswith(prefix):
            u = u[len(prefix) :]
    u = u.split("/")[0].split("?")[0].split("#")[0].lower()
    if u.startswith("www."):
        u = u[4:]
    return u


def _source_is_primary(url: str) -> bool:
    """True when the source's domain is a primary/high-signal surface."""
    host = _normalize_source_domain(url)
    return bool(host and any(m in host for m in _PRIMARY_SOURCE_MARKERS))


def _links_to_plain_text(text: str) -> str:
    """Convert markdown ``[label](url)`` links to plain ``label — url``.

    The Telegram delivery pipeline (escape → ``_markdown_to_html``) has no
    link pass, so a model-emitted ``[title](url)`` would render literally.
    Converting to plain text keeps the URL tappable (Telegram auto-linkifies
    bare URLs) without markdown leakage. Applied to every answer path
    (backend + both remote tiers) before the source block is appended.
    """
    return _MD_LINK_RE.sub(lambda m: f"{m.group(1).strip()} \u2014 {m.group(2).strip()}", text)


def _append_source_block(
    answer: str,
    sources: list[dict[str, str]] | None,
    limit: int = 5,
) -> str:
    """Append a compact "Sources:" block to a remote reply.

    Surfaces up to ``limit`` (default 5) grounding sources as PLAIN TEXT
    (``title — url``), de-duplicated by normalized domain (one entry per
    site) and ranked so primary/high-signal surfaces (official docs,
    block explorers, major outlets) render FIRST — fallback answers cite
    where the intel came from. The remote-path twin of
    ``_format._append_source_lines``. Plain text,
    NOT markdown links (``[title](url)``) and NOT raw ``<a href>`` HTML:
    both render literally in the final message because ``_send_answer``
    HTML-escapes every chunk and ``_markdown_to_html`` has no link pass
    (the code-review note on ``[title](url)`` showing verbatim). A bare URL
    is auto-linkified by Telegram even in HTML parse mode, so the source
    stays clickable. ``sources`` may carry ``title``/``url``, or the
    bridge's ``type``+``query`` metadata — the query doubles as the title
    when no real title exists, and a source WITHOUT a ``url`` renders as
    plain text (never a fabricated/dead link). Titles are NOT pre-escaped
    here (``_send_answer``'s ``html.escape`` is the single escape layer for
    this untrusted bridge metadata — pre-escaping would double-escape
    ``&``, same pattern as ``_append_source_lines``); markdown emphasis
    markers are stripped from titles so the escape → ``_markdown_to_html``
    delivery pipeline never mis-renders them. Returns the answer unchanged
    when ``sources`` is empty/None.
    """
    if not sources:
        return answer
    # De-duplicate by normalized domain (URL-bearing sources only — a source
    # without a URL renders as a plain title and is never deduped). The first
    # occurrence per domain wins.
    seen_domains: set[str] = set()
    deduped: list[dict[str, str]] = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url") or "").strip()
        domain = _normalize_source_domain(url) if url else ""
        if domain and domain in seen_domains:
            continue
        if domain:
            seen_domains.add(domain)
        deduped.append(s)
    # Rank: primary/high-signal domains first, stable within each group.
    deduped.sort(key=lambda s: 0 if _source_is_primary(str(s.get("url") or "")) else 1)
    rendered: list[str] = []
    for s in deduped:
        if len(rendered) >= limit:
            break
        title = str(s.get("title") or s.get("query") or "").strip()[:80]
        if not title:
            continue
        # Plain-text titles: markdown links → inner text, then drop the
        # remaining markdown/link syntax so the escape → _markdown_to_html
        # pipeline never mis-renders them.
        title = _MD_LINK_RE.sub(r"\1", title)
        title = re.sub(r"[*_`\[\]]", "", title).strip()
        if not title:
            continue
        url = str(s.get("url") or "").strip()[:200]
        if url:
            # URLs stay tappable: keep underscores (word-boundary guarded by
            # _markdown_to_html) but drop the rare `*`/backtick markers that
            # WOULD mis-render into bold/code spans.
            url = re.sub(r"[*`]", "", url).strip()
            rendered.append(f"{title} \u2014 {url}")
        else:
            rendered.append(title)
    if not rendered:
        return answer
    return f"{answer}\n\n\U0001f4da Sources: " + ", ".join(rendered)
