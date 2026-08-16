"""LLM clients — tier router + question-type router + context gathers.

Deterministic routing: ``_route_tier`` picks snappy/long (mirrors the
backend ``_route_task``); ``_classify_question`` + ``_gather_context``
retrieve 1-2 relevant sources (price / scanner / RAG KB / news_v2 + live
SearXNG fallback) so the remote LLM answers with real specifics. Every
gather is bounded and fail-open. ``_gather_web_fallback`` resolves
through the package so tests can patch ``chat_ai._llm._gather_web_fallback``
(monkeypatch contract).
"""

from __future__ import annotations

import asyncio
import html

from app.domains.telegram.rugmunchbot.handlers.chat_ai import _llm as _llm_mod
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT,
    _CONTEXT_MAX_CHARS,
    _CTX_EDU_RE,
    _CTX_MARKET_RE,
    _CTX_NEWS_RE,
    _CTX_SCAM_RE,
    _CTX_TA_RE,
    _CTX_TOOL_SETS,
    _CTX_TRENDING_RE,
    _EDU_ANSWER_HINT,
    _EDU_RAG_THIN_CHARS,
    _EDU_RAG_THIN_HITS,
    _NEWS_THIN_THRESHOLD,
    _TA_ANSWER_HINT,
    _TIER_ADDRESS_OR_HASH_RE,
    _TIER_ANALYSIS_FORENSIC_RE,
    _TIER_ANALYSIS_LENGTH_THRESHOLD,
    _TIER_ANALYSIS_QUESTION_RE,
    _TIER_ANALYSIS_TERMS,
    _TIER_DEFINITIONAL_RE,
    _WEB_FALLBACK_TIMEOUT,
    _WEB_RESULTS_MAX,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._sources import _normalize_source_domain
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.guardrails import extract_addresses
from app.domains.telegram.rugmunchbot.tools.intent import build_tool_prompt_block, tool_kwargs

logger = get_logger("rmi_bot.chat_ai")


def _route_tier(message: str) -> str:
    """Route ``message`` to a remote provider tier: ``"snappy"`` (fast short
    chat answers) or ``"long"`` (longer/reasoning analysis). Mirrors the
    backend ``_route_task`` so bot and backend agree (snappy = old 4B chat
    tier / long = old 9B analysis tier)."""
    low = message.lower().strip()
    if not low:
        return "snappy"
    if len(low) > _TIER_ANALYSIS_LENGTH_THRESHOLD:
        return "long"
    if _TIER_ADDRESS_OR_HASH_RE.search(low):
        return "long"
    if _TIER_ANALYSIS_FORENSIC_RE.search(low):
        return "long"
    if _TIER_DEFINITIONAL_RE.search(low):
        return "snappy"
    if _TIER_ANALYSIS_QUESTION_RE.search(low):
        return "long"
    if any(term in low for term in _TIER_ANALYSIS_TERMS):
        return "long"
    return "snappy"


def _classify_question(question: str) -> str:
    """Deterministic question-TYPE classifier (regex only, no LLM).

    Returns ``""`` when no type matches (greetings / generic asks — the LLM
    answers from its own knowledge). Address presence is the strongest
    signal (SPECIFIC TOKEN beats every keyword type); SCAM/SAFETY beats
    education because "what is a honeypot" is equally answered by the
    security-pattern KB (rag_lookup) that both types share.
    """
    if not question or not question.strip():
        return ""
    low = question.lower()
    if extract_addresses(question):
        return "token"
    if _CTX_SCAM_RE.search(low):
        return "scam"
    if _CTX_NEWS_RE.search(low):
        return "news"
    if _CTX_MARKET_RE.search(low):
        return "market"
    if _CTX_EDU_RE.search(low):
        return "education"
    return ""


async def _gather_rag_news_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """RAG news_v2 hits via the existing ``app.rag.tools.get_news`` facade.

    Fail-open: any failure (import, timeout, empty hits) returns empty
    context so the LLM still answers from its own knowledge.
    """
    try:
        from app.rag.tools import get_news as _rag_get_news

        news = await asyncio.wait_for(_rag_get_news(question, top_k=5), timeout=_CONTEXT_GATHER_TIMEOUT)
    except Exception as exc:
        logger.debug("question-router news gather failed (query %.60s): %s", question, exc)
        return "", []
    if not isinstance(news, dict):
        return "", []
    hits = news.get("results")
    if not isinstance(hits, list) or not hits:
        return "", []
    lines = ["RAG NEWS (news_v2 — real articles; cite them by source/URL):"]
    sources: list[dict[str, str]] = []
    for h in hits[:5]:
        content = str(h.get("content") or "").strip()[:220]
        url = str(h.get("canonical_url") or "").strip()
        if not content:
            continue
        label = str(h.get("source") or url or "news article")[:60]
        lines.append(f"- {content} (source: {label})")
        if url:
            sources.append({"type": "news", "title": content[:60], "url": url})
    if len(lines) == 1:
        return "", []
    return "\n".join(lines)[:_CONTEXT_MAX_CHARS], sources


async def _gather_news_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """NEWS context: RAG news_v2 hits, PLUS live SearXNG results when the
    news_v2 corpus is thin (< ``_NEWS_THIN_THRESHOLD`` hits).

    Current-event questions ("what's happening", "latest news", "market
    today") need CURRENT, sourced intel — a thin RAG corpus cannot provide
    that, so the router falls through to the live web layer. Both blocks are
    plain text (the caller wraps them in the existing ``<untrusted_context>``
    delimiter) and their sources merge into the grounding footer.

    The RAG fetch and the SearXNG web layer run CONCURRENTLY (same pattern
    as ``_gather_edu_lore_context``): the news_v2 search can take ~5s on the
    live backend and the web layer answers in ~1s — never serialize the two
    (the old path could stack to ~8.5s inside a 5s router budget).
    """
    rag_task = asyncio.create_task(_gather_rag_news_context(question))
    web_task = asyncio.create_task(_llm_mod._gather_web_fallback(question))
    try:
        # FIRST_COMPLETED: the web layer answers fast while the news_v2
        # corpus search may take the full budget — never stall on the slow
        # RAG fetch. If RAG lands first with ≥2 hits the web fetch is
        # dropped; otherwise give the web layer the REST of the budget.
        done, pending = await asyncio.wait(
            {rag_task, web_task},
            timeout=_CONTEXT_GATHER_TIMEOUT,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if rag_task in done and web_task in pending:
            await asyncio.wait({web_task}, timeout=max(0.0, _CONTEXT_GATHER_TIMEOUT - 0.2))
    except Exception as exc:
        logger.debug("question-router news gather failed (query %.60s): %s", question, exc)
    rag_block, rag_sources = "", []
    web_block, web_sources = "", []
    try:
        if not rag_task.cancelled() and rag_task.done():
            rag_block, rag_sources = rag_task.result()
    except Exception as exc:
        logger.debug("question-router news RAG layer failed (query %.60s): %s", question, exc)
    try:
        if not web_task.cancelled() and web_task.done():
            web_block, web_sources = web_task.result()
    except Exception as exc:
        logger.debug("question-router news web layer failed (query %.60s): %s", question, exc)
    finally:
        for t in (rag_task, web_task):
            if not t.done():
                t.cancel()
        if rag_task.cancelled() or web_task.cancelled():
            await asyncio.gather(rag_task, web_task, return_exceptions=True)
    if len(rag_sources) >= _NEWS_THIN_THRESHOLD:
        return rag_block, rag_sources
    parts = [b for b in (rag_block, web_block) if b]
    if not parts:
        return "", []
    return "\n\n".join(parts)[:_CONTEXT_MAX_CHARS], rag_sources + web_sources


def _render_web_results(results: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Render SearXNG results as an HTML-escaped context block + sources.

    Web snippets/pages are UNTRUSTED data — a page could embed markup that
    tries to inject HTML into the final Telegram answer, so every field
    (snippet, title, URL) is ``html.escape``d here; the caller wraps the
    block in the ``<untrusted_context>`` delimiter and the sources flow into
    the grounding footer (``_append_source_block`` renders them as plain
    ``title — url`` text; ``_send_answer``'s single ``html.escape`` is the
    escape layer). Returns ``("", [])`` when nothing usable survived.
    """
    lines = ["LIVE WEB SEARCH (real-time web results — cite by source/URL):"]
    sources: list[dict[str, str]] = []
    seen_domains: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        title = str(r.get("title") or "").strip()
        snippet = str(r.get("snippet") or "").strip()
        if not url:
            continue
        # One page per domain — both the context block the model reads and
        # the sources footer stay clean (first result per domain wins).
        domain = _normalize_source_domain(url)
        if domain and domain in seen_domains:
            continue
        if domain:
            seen_domains.add(domain)
        text = (snippet or title).strip()
        if not text:
            continue
        label = (title or url)[:60]
        lines.append(f"- {html.escape(text[:200])} (source: {html.escape(label)} — {html.escape(url)})")
        sources.append({"type": "web", "title": text[:60], "url": url})
    if len(lines) == 1:
        return "", []
    return "\n".join(lines)[:_CONTEXT_MAX_CHARS], sources


async def _gather_web_fallback(question: str) -> tuple[str, list[dict[str, str]]]:
    """SearXNG web fallback for thin/empty router sources (fail-open).

    Uses ``tools.web.searxng_top_results`` (SearXNG-only — no bridge, no
    knowledge LLM), bounded by ``_WEB_FALLBACK_TIMEOUT`` so the router stays
    close to its ~5s snappy budget. Any failure returns empty context and
    the LLM answers from its own knowledge.
    """
    try:
        from app.domains.telegram.rugmunchbot.tools.web import searxng_top_results

        results = await asyncio.wait_for(
            searxng_top_results(question, limit=_WEB_RESULTS_MAX),
            timeout=_WEB_FALLBACK_TIMEOUT,
        )
    except Exception as exc:
        logger.debug("question-router web fallback failed (query %.60s): %s", question, exc)
        return "", []
    if not results:
        return "", []
    return _render_web_results(results)


async def _gather_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """Thin question router: retrieve 1-2 relevant sources for ``question``.

    Returns ``(context_block, sources)`` — both empty when the type is
    unknown, no source answered, or the 5s budget expired (fail-open). The
    caller passes these into the remote LLM's existing ``sources`` param and
    the separately-budgeted RETRIEVED CONTEXT message, so the model answers
    with real data instead of generic knowledge.

    Type → source map (implemented):
      * MARKET/TA      → price_lookup (+ technical_analysis / trending);
                         live web results when the tool data is thin
      * SPECIFIC TOKEN → scanner_intel + price_lookup (address present)
      * SCAM/SAFETY    → rag_lookup security-pattern KB (+ scanner_intel when address)
      * EDUCATION/LORE → rag_lookup intel KB (security_patterns / defi_exploits / scam_patterns);
                         live web results when the KB is thin
      * NEWS           → RAG news_v2 via app.rag.tools.get_news, PLUS live
                         SearXNG results when the news_v2 corpus is thin
      Web results are HTML-escaped, untrusted-wrapped (``<untrusted_context>``
      by the caller) and cited as sources (``type: "web"``).
    """
    qtype = _classify_question(question)
    if not qtype:
        return "", []
    if qtype == "news":
        return await _gather_news_context(question)
    # EDUCATION / LORE (qtype "education" or address-less "scam") are pure
    # knowledge asks — they answer from the RAG intel KB (rag_lookup) with a
    # CONCURRENT SearXNG fallback (the KB search can take ~5s on the live
    # backend — it must never starve the web layer), plus the education
    # answer-structure hint (see ``_gather_edu_lore_context``).
    if qtype in ("education", "scam"):
        return await _gather_edu_lore_context(question)

    names: list[str] = list(_CTX_TOOL_SETS.get(qtype, []))
    low = question.lower()
    # TA-typed question → run technical_analysis alongside price_lookup and
    # append the TA answer-format hint to the retrieved context (see
    # ``_TA_ANSWER_HINT``). TRENDING questions get the trending tool instead.
    is_ta_question = qtype == "market" and bool(_CTX_TA_RE.search(low))
    if qtype == "market":
        if is_ta_question and "technical_analysis" not in names:
            names.append("technical_analysis")
        elif _CTX_TRENDING_RE.search(low) and "trending" not in names:
            names.append("trending")
    # scanner_intel needs an address — drop it when the question names none
    # (avoids a pointless "address required" miss).
    if not extract_addresses(question):
        names = [n for n in names if n != "scanner_intel"]
    if not names:
        return "", []

    spec = tool_kwargs(question, names)
    if "rag_lookup" in names:
        spec["rag_lookup"] = {"query": question}
    try:
        from app.domains.telegram.rugmunchbot.tools import get_registry

        results = await asyncio.wait_for(get_registry().run_many_kwargs(spec), timeout=_CONTEXT_GATHER_TIMEOUT)
    except Exception as exc:
        logger.debug("question-router context gather failed (query %.60s): %s", question, exc)
        return "", []

    ok_results = {name: r for name, r in results.items() if r.ok}
    if not ok_results:
        # MARKET/TA or LORE question with thin/empty tool data → pull CURRENT
        # web results so the LLM still gets sourced context instead of
        # answering from training memory (fail-open: "" when SearXNG is
        # down too).
        return await _llm_mod._gather_web_fallback(question)
    try:
        block = build_tool_prompt_block(ok_results)
        if is_ta_question:
            # Reserve room for the hint so it always survives truncation.
            block = block[: max(0, _CONTEXT_MAX_CHARS - len(_TA_ANSWER_HINT) - 2)]
            block = f"{block}\n\n{_TA_ANSWER_HINT}"
        else:
            block = block[:_CONTEXT_MAX_CHARS]
    except Exception as exc:
        logger.debug("question-router prompt block failed (query %.60s): %s", question, exc)
        return "", []
    sources = [
        {"type": name, "query": question[:90], "title": f"{name} \u00b7 {r.source or 'live data'}"}
        for name, r in ok_results.items()
    ]
    return block, sources


async def _gather_edu_lore_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """EDUCATION / LORE context: RAG intel KB + CONCURRENT live web layer.

    Runs ``rag_lookup`` (intel KB: security_patterns / defi_exploits /
    scam_patterns) and the SearXNG web fallback CONCURRENTLY so the slow KB
    search (measured ~5s on the live backend) can never consume the whole
    router budget and starve the web layer. The web layer is MERGED when the
    KB is missing or thin (``_EDU_RAG_THIN_HITS`` / ``_EDU_RAG_THIN_CHARS``);
    a rich KB stands alone. Either way the education answer-structure hint
    (plain-language explanation → concrete example → risk/why-it-matters) is
    appended to the RETRIEVED CONTEXT block. Fail-open: any failure returns
    empty context and the LLM answers from its own knowledge.
    """
    from app.domains.telegram.rugmunchbot.tools import get_registry
    from app.domains.telegram.rugmunchbot.tools.intent import tool_kwargs

    spec = tool_kwargs(question, ["rag_lookup"])
    spec["rag_lookup"] = {"query": question}
    try:
        rag_task = asyncio.create_task(get_registry().run_many_kwargs(spec))
        web_task = asyncio.create_task(_llm_mod._gather_web_fallback(question))
    except Exception as exc:
        logger.debug("question-router edu/lore gather failed (query %.60s): %s", question, exc)
        return "", []
    try:
        # FIRST_COMPLETED: the live SearXNG layer answers in ~1s while the KB
        # search can take ~5s+ — never stall the answer on the slow KB. If the
        # KB completes first (fast backend, cache), its hits are used;
        # otherwise the web layer stands alone (fail-open: timeout caps the
        # total wait).
        done, pending = await asyncio.wait(
            {rag_task, web_task},
            timeout=_CONTEXT_GATHER_TIMEOUT,
            return_when=asyncio.FIRST_COMPLETED,
        )
        # RAG finished (or failed instantly via negative cache) but the web
        # fetch is still in flight — give the web layer the REST of the
        # router budget so a thin/absent KB never leaves the answer empty.
        if rag_task in done and web_task in pending:
            await asyncio.wait({web_task}, timeout=max(0.0, _CONTEXT_GATHER_TIMEOUT - 0.2))
    except Exception as exc:
        logger.debug("question-router edu/lore gather failed (query %.60s): %s", question, exc)
    # Collect whatever finished within the budget (fail-open on the rest).
    web_block, web_sources = "", []
    try:
        if not web_task.cancelled() and web_task.done():
            web_block, web_sources = web_task.result()
    except Exception as exc:
        logger.debug("question-router web layer failed (query %.60s): %s", question, exc)
    rag_block, rag_hits = "", []
    try:
        if not rag_task.cancelled() and rag_task.done():
            rag_results = rag_task.result() or {}
            rag_result = rag_results.get("rag_lookup")
            if rag_result is not None and getattr(rag_result, "ok", False):
                rag_data = rag_result.data if isinstance(rag_result.data, dict) else {}
                rag_hits = rag_data.get("hits") or []
                rag_block = str(rag_data.get("context") or "")
    except Exception as exc:
        logger.debug("question-router RAG layer failed (query %.60s): %s", question, exc)
    finally:
        for t in (rag_task, web_task):
            if not t.done():
                t.cancel()
        if rag_task.cancelled() or web_task.cancelled():
            await asyncio.gather(rag_task, web_task, return_exceptions=True)

    rag_thin = not rag_block or len(rag_hits) < _EDU_RAG_THIN_HITS or len(rag_block) < _EDU_RAG_THIN_CHARS
    if rag_thin and web_block:
        block = "\n\n".join(b for b in (rag_block, web_block) if b)
        sources = [
            *[{"type": "rag_lookup", "query": question[:90], "title": "rag_lookup \u00b7 intel KB"}],
            *web_sources,
        ]
        if not rag_block:
            sources = web_sources
    elif rag_block:
        block = rag_block
        sources = [{"type": "rag_lookup", "query": question[:90], "title": "rag_lookup \u00b7 intel KB"}]
    else:
        return "", []
    # Reserve room for the hint so it always survives truncation.
    block = block[: max(0, _CONTEXT_MAX_CHARS - len(_EDU_ANSWER_HINT) - 2)]
    if not block:
        return "", []
    return f"{block}\n\n{_EDU_ANSWER_HINT}", sources
