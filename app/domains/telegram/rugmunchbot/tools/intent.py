"""Intent routing — decide which tools to run for a user question.

Deterministic, cheap, and fast (no LLM round-trip for routing): keyword +
pattern matching on the user's question. The tools themselves always return
REAL data; the router only decides WHICH tools are relevant.

Intents map to tools:
  * trending / hot / what's pumping    -> trending (+ news_headlines)
  * $SYMBOL / price of X               -> price_lookup
  * who's buying / smart money / top traders -> top_traders, smart_money
  * scan / risk / honeypot + address   -> security_scan
  * wallet / holdings / pnl + address  -> wallet_analysis
  * news / market context / general    -> web_search, trending, news_headlines
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from app.domains.telegram.rugmunchbot.formatters._base import TOKEN_RE as SYMBOL_RE
from app.domains.telegram.rugmunchbot.tools.guardrails import EVM_RE, SOL_RE

_TRENDING_WORDS = re.compile(
    r"\b(trending|hot|pump|pumping|movers|gainers|top tokens?|what'?s? (hot|pumping|moving)|new launches|launching)\b",
    re.IGNORECASE,
)
_PRICE_WORDS = re.compile(
    r"\b(price|worth|value|cost|how much|chart|pump|dump|market cap|mcap|fdv|trading at)\b",
    re.IGNORECASE,
)
_SMART_MONEY_WORDS = re.compile(
    r"\b(smart money|smart-money|who'?s? buying|who is buying|buying|buyers|top traders?|top holders?|whales?|accumulating|early buyers)\b",
    re.IGNORECASE,
)
_SECURITY_WORDS = re.compile(
    r"\b(scan|audit|honeypot|rug|risk|safe|scam|security|malicious|token check|is it a)\b",
    re.IGNORECASE,
)
_WALLET_WORDS = re.compile(
    r"\b(wallet|holdings|portfolio|pnl|profit|loss|balance|tokens in)\b",
    re.IGNORECASE,
)
_NEWS_WORDS = re.compile(
    r"\b(news|headlines|market (today|now)|context|what'?s? happening|update|latest)\b",
    re.IGNORECASE,
)


def extract_addresses(text: str) -> list[str]:
    """EVM/Solana addresses present in the question (deduped)."""
    found: list[str] = []
    for m in EVM_RE.finditer(text):
        found.append(m.group(0))
    for m in SOL_RE.finditer(text):
        if m.group(0) not in found:
            found.append(m.group(0))
    return found


def has_symbol(text: str) -> bool:
    return bool(SYMBOL_RE.search(text))


def symbols_in(text: str) -> list[str]:
    return [m.group(1).upper() for m in SYMBOL_RE.finditer(text)]


def route_tools(text: str) -> list[str]:
    """Return the ordered list of tools to run for ``text``.

    Order matters: the highest-signal intent comes first. The caller may
    still run them concurrently; the order only drives prompt layout.
    """
    tools: list[str] = []
    addrs = extract_addresses(text)
    has_ticker = has_symbol(text)

    if _TRENDING_WORDS.search(text):
        tools.append("trending")
        if _NEWS_WORDS.search(text):
            tools.append("news_headlines")

    if has_ticker or _PRICE_WORDS.search(text):
        tools.append("price_lookup")

    if _SMART_MONEY_WORDS.search(text):
        tools.append("top_traders")
        tools.append("smart_money")

    if addrs and _SECURITY_WORDS.search(text):
        tools.append("security_scan")

    if addrs and _WALLET_WORDS.search(text):
        tools.append("wallet_analysis")

    # General knowledge / news / market context questions get live web context.
    if _NEWS_WORDS.search(text) and "trending" not in tools:
        tools.append("web_search")

    # A bare address with no other intent is a security question.
    if addrs and "security_scan" not in tools and "wallet_analysis" not in tools:
        tools.append("security_scan")

    # Deduplicate preserving order.
    return list(dict.fromkeys(tools))


def build_tool_prompt_block(results: Mapping[str, object]) -> str:
    """Render tool results as a compact LIVE DATA block for the LLM prompt.

    Only REAL tool data is included (ok=True). The block labels the source
    so the model attributes and never presents tool data as its own memory.
    ``Mapping`` (covariant) so callers can pass ``dict[str, ToolResult]``.
    """
    lines: list[str] = ["LIVE MARKET DATA (fetched just now from real APIs):"]
    for name in sorted(results):
        result = results[name]
        if getattr(result, "ok", False) is False:
            continue
        from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

        if not isinstance(result, ToolResult):
            continue
        source = result.source or name
        lines.append(f"- {name} [source: {source}]:")
        payload = result.data
        if isinstance(payload, dict):
            for k, v in payload.items():
                text = str(v) if not isinstance(v, (dict, list)) else str(v)[:500]
                lines.append(f"    {k}: {text[:500]}")
        else:
            lines.append(f"    {str(payload)[:800]}")
    lines.append(
        "You may ONLY cite symbols, addresses, prices, and metrics that appear in this LIVE MARKET DATA. If the user asks for data not present here, say you don't have live data for it."
    )
    return "\n".join(lines)
