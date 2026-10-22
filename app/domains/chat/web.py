"""SearXNG fallback + lightweight page extraction for current affairs.

Used only when the RAG rerank produces no passage above the floor. Extracts
up to three pages so each web citation carries real text, not just a SERP
snippet. Every failure returns ``[]`` — the caller then serves a refusal.

SSRF guard: only http/https, and the hostname must resolve to a public IP.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

_UA = "RugMunchIntelligence/1.0 (+https://rugmunch.io)"
_MAX_BYTES = 1_500_000
_MAX_CHARS = 1200
_FETCH_TIMEOUT = 6.0
_PAGE_LIMIT = 3

_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")


def _is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


async def _safe_url(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if not host or host.lower() in {"localhost", "metadata.google.internal"}:
        return False
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _is_public, host), 3.0)
    except Exception:
        return False


def _html_to_text(raw: str) -> str:
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return _BLANK_RE.sub("\n\n", text).strip()[:_MAX_CHARS]


async def _fetch(url: str) -> str:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(_FETCH_TIMEOUT, connect=3.0), follow_redirects=True
    ) as client:
        resp = await client.get(url, headers={"User-Agent": _UA})
    if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", "").lower():
        return ""
    return _html_to_text(resp.text[:_MAX_BYTES])


async def extract_page(url: str) -> str:
    """Return cleaned page text, or ``""`` when unsafe/unreachable/empty."""
    if not await _safe_url(url):
        return ""
    try:
        return await _fetch(url)
    except Exception as exc:
        log.warning("chat_web_extract_failed err=%s", type(exc).__name__)
        return ""


async def search_and_extract(query: str, max_pages: int = _PAGE_LIMIT) -> list:
    """SearXNG search, then extract up to ``max_pages`` results into passages."""
    from app.domains.chat.retrieval import Passage

    try:
        from app.core.web_search import searxng_search

        results = await searxng_search(query, max_results=max_pages + 2)
    except Exception as exc:
        log.warning("chat_web_search_failed err=%s", type(exc).__name__)
        return []
    candidates = [r for r in (results or []) if r.get("url")][:max_pages]
    if not candidates:
        return []
    texts = await asyncio.gather(*(extract_page(r["url"]) for r in candidates))
    passages = []
    for item, text in zip(candidates, texts, strict=False):
        body = text or str(item.get("content") or "").strip()
        if len(body) < 40:
            continue
        passages.append(
            Passage(
                text=body[:_MAX_CHARS],
                title=str(item.get("title") or item["url"])[:200],
                url=item["url"],
                library_id=None,
                source="web",
                score=1.0,
            )
        )
    return passages[:max_pages]
