"""Intent routing — decide which tools to run for a user question.

Deterministic, cheap, and fast (no LLM round-trip for routing): keyword +
pattern matching on the user's question. The tools themselves always return
REAL data; the router only decides WHICH tools are relevant.

Intents map to tools:
  * trending / hot / what's pumping    -> trending (+ news_headlines, + trend_by_chain when a chain is named)
  * $SYMBOL / price of X               -> price_lookup
  * who's buying / smart money / top traders -> top_traders (needs address), smart_money, birdeye_market
  * scan / risk / honeypot + address   -> scanner_intel, security_scan, token_meta (image)
  * wallet / holdings / pnl + address  -> wallet_analysis
  * prediction markets / polymarket / odds on X -> prediction_markets
  * market overview / how's the market / fear and greed / btc price -> market_overview (+ price_lookup)
  * news / market context / general    -> web_search, trending, news_headlines

``route_tools`` decides WHICH tools run; ``tool_kwargs`` maps each selected
tool to the kwargs it understands (address / symbol / chain / kind). Both are
pure and deterministic — no LLM round-trip, no symbol synthesis.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

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
_PREDICTION_WORDS = re.compile(
    r"\b(prediction markets?|polymarket|kalshi|odds (on|for|of)|who will win|chances of|probability of)\b",
    re.IGNORECASE,
)
_MARKET_OVERVIEW_WORDS = re.compile(
    r"\b(market overview|how('s| is| are)? (the )?market|crypto market|fear and greed|"
    r"market (status|update|today)|btc price|bitcoin price|eth price|sol price|"
    r"price of (btc|bitcoin|eth|sol|solana))\b",
    re.IGNORECASE,
)

# Chain-name extraction: canonical aliases -> DexScreener-style chain id.
# Mirrors trend_algo._CHAIN_ALIASES (keep in sync). Word-boundary anchored so
# "op" never matches inside "top" or "polymarket".
_CHAIN_ALIASES: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "solana": "solana",
    "sol": "solana",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "binance-smart-chain": "bsc",
    "binance": "bsc",
    "bsc": "bsc",
    "bnb": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "optimism": "optimism",
    "op": "optimism",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "berachain": "berachain",
    "bera": "berachain",
    "hyperliquid": "hyperliquid",
    "zksync": "zksync",
    "robinhood": "robinhood",
    "injective": "injective",
    "fantom": "fantom",
    "ftm": "fantom",
    "linea": "linea",
    "blast": "blast",
    "sonic": "sonic",
    "scroll": "scroll",
    "mantle": "mantle",
    "tron": "tron",
    "trx": "tron",
    "ton": "ton",
    "base": "base",
    "sei": "sei",
    "sui": "sui",
    "aptos": "aptos",
    "mode": "mode",
}

_CHAIN_RE = re.compile(
    r"\b(" + "|".join(sorted(_CHAIN_ALIASES, key=len, reverse=True)) + r")\b",
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


def extract_chain(text: str) -> str:
    """First canonical chain name mentioned in the question ('' if none).

    Returns the canonical DexScreener-style chain id ("solana", "ethereum",
    "base", "bsc", ...) or '' when no chain alias is present. Pure regex —
    never infers a chain from an address prefix.
    """
    m = _CHAIN_RE.search(text)
    if not m:
        return ""
    return _CHAIN_ALIASES[m.group(1).lower()]


def route_tools(text: str) -> list[str]:
    """Return the ordered list of tools to run for ``text``.

    Order matters: the highest-signal intent comes first. The caller may
    still run them concurrently; the order only drives prompt layout.

    Deterministic and cheap (regex/keyword only, no LLM). Tools that need an
    on-chain address (scanner_intel, token_meta, security_scan, top_traders,
    wallet_analysis) are only routed when an address is actually present —
    never route a tool that cannot answer.
    """
    tools: list[str] = []
    addrs = extract_addresses(text)
    has_ticker = has_symbol(text)
    chain = extract_chain(text)

    # Trending: cross-chain feed; when a chain is named also run the
    # per-chain trend_algo (transparent scoring over our data).
    if _TRENDING_WORDS.search(text):
        tools.append("trending")
        if _NEWS_WORDS.search(text):
            tools.append("news_headlines")
        if chain:
            tools.append("trend_by_chain")

    if has_ticker or _PRICE_WORDS.search(text):
        tools.append("price_lookup")

    if _PREDICTION_WORDS.search(text):
        tools.append("prediction_markets")

    if _MARKET_OVERVIEW_WORDS.search(text):
        tools.append("market_overview")

    # Smart money / top traders: on-chain feeds + Birdeye (kind resolved in
    # tool_kwargs: top_traders needs a token address, smart_money does not).
    if _SMART_MONEY_WORDS.search(text):
        if addrs:
            tools.append("top_traders")
        tools.append("smart_money")
        tools.append("birdeye_market")

    if addrs and _SECURITY_WORDS.search(text):
        tools.append("scanner_intel")
        tools.append("security_scan")
        tools.append("token_meta")

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


def tool_kwargs(text: str, names: list[str]) -> dict[str, dict[str, Any]]:
    """Per-tool kwargs map: each selected tool gets the params it understands.

    Mirrors the extraction ``run_for_message`` used to do inline (addresses,
    ``$SYMBOL`` mentions) plus chain + Birdeye ``kind``. Routing and the
    kwargs map live together here so a new tool needs exactly one change to
    become reachable AND correctly parameterized.

    Args:
        text: The user's question (source of extracted values).
        names: Tool names returned by :func:`route_tools`.

    Returns:
        ``{tool_name: kwargs}`` — the map consumed by
        ``registry.run_many_kwargs`` (shared ctx is merged by the registry).
    """
    addrs = extract_addresses(text)
    syms = symbols_in(text)
    chain = extract_chain(text)

    per_tool: dict[str, dict[str, Any]] = {}
    for name in names:
        kwargs: dict[str, Any] = {}
        if name in (
            "price_lookup",
            "scanner_intel",
            "security_scan",
            "token_meta",
            "top_traders",
            "wallet_analysis",
            "birdeye_market",
        ):
            kwargs["address"] = addrs[0] if addrs else ""
            kwargs["chain"] = chain
        # Address takes precedence; a $SYMBOL fills in when no address is given.
        if name in ("price_lookup", "token_meta") and syms and not addrs:
            kwargs["symbol"] = syms[0]
        if name == "trend_by_chain":
            kwargs["chain"] = chain or "all"
        if name == "smart_money":
            kwargs["chain"] = chain or "solana"
        if name == "birdeye_market":
            kwargs["kind"] = "top_traders" if addrs else "smart_money"
            kwargs["chain"] = chain or "solana"
        if name == "web_search":
            kwargs["query"] = text
        per_tool[name] = kwargs
    return per_tool


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
