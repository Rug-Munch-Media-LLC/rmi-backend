"""Crypto skill router for the chat/telegram surface — pure, deterministic, no I/O.

One question in, one :class:`SkillPlan` out: intent class, retrieval depth, the
data tool that can answer it, the answer lane allowed to phrase it, token budget,
cache TTL.

Most crypto questions are a few shapes and the cheap shapes are the frequent
ones: a price ask is a deterministic card, a token-risk ask is the scanner's
verdict plus a template, and only exploit/news or genuinely ambiguous questions
need a model that reasons. That split keeps model tokens off most of the traffic.

Constraints: stdlib only, no ``app`` imports (the bot, the web route and the tests
import this without booting ``app.config``); signals live in
:mod:`app.domains.chat._crypto_signals`. Pure — no network, clock or randomness.
Plans are frozen and shared. The fail-safe default is ``unknown``: shallow
retrieval, fast lane, refusal-capable rubric, because a cheap refusal is
recoverable and a confident scam answer is not.

Ordering is the design, first match wins:
``portfolio -> wallet -> exploit -> address -> definition -> risk -> market ->
price -> howto``. An address alone is a risk ask; an address plus a forensics
word is a wallet ask; an address plus "my bags" is a portfolio ask.

Table pinned by ``tests/unit/test_chat_skill_router.py``, mirrored in
``docs/CRYPTO-SKILL-ROUTER.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from . import _crypto_signals as sig

TOKEN_RISK, WALLET, PRICE, MARKET = "token_risk", "wallet", "price", "market"
EXPLOIT, HOWTO, PORTFOLIO, UNKNOWN = "exploit", "howto", "portfolio", "unknown"

LANE_TEMPLATE: Final = "template"  # no model: rendered data card
LANE_FAST: Final = "local_fast"  # local extract lane, short answer
LANE_DEEP: Final = "local_deep"  # local reasoning lane, one call

LANE_MODEL: Final = {
    LANE_TEMPLATE: "none (deterministic card)",
    LANE_FAST: "local:8015 extract lane, free-tier chain as fallback",
    LANE_DEEP: "local:8009 Qwen3.6 workhorse, single call",
}

# Reference prices for the "what this would cost on a paid API" column only;
# every lane this router can select is local, so marginal cost is 0.0.
REF_IN_USD_PER_MTOK: Final = 0.15
REF_OUT_USD_PER_MTOK: Final = 0.60
PROMPT_OVERHEAD_TOKENS: Final = 420  # guardrails + rubrics + user turn

# Rubric sets, resolved against ``_crypto_skills.RUBRICS`` by the caller.
RISK_RUBRICS: Final = (
    "risk_explanation",
    "evidence_citation",
    "insufficient_data",
    "scam_caution",
)
CITE_RUBRICS: Final = ("evidence_citation", "insufficient_data")
MONEY_RUBRICS: Final = ("evidence_citation", "no_price_prediction")


@dataclass(frozen=True)
class SkillPlan:
    """One routing decision. ``depth``: 0 cache | 1 market/label cache | 2 corpus k=4
    | 3 corpus + scanner/trace | 4 corpus + exploit corpus + web."""

    intent: str
    depth: int
    tools: tuple[str, ...]  # chat tool executor names; empty = no data tool
    lane: str
    max_tokens: int
    retrieval_tokens: int
    cache_ttl_s: int
    skills: tuple[str, ...] = ()  # rubric keys from ``_crypto_skills``
    cite_required: bool = True
    refuse_when_empty: bool = True

    @property
    def model(self) -> str:
        return LANE_MODEL[self.lane]

    @property
    def uses_model(self) -> bool:
        return self.lane != LANE_TEMPLATE


# intent, depth, tools, lane, max_tokens, retrieval_tokens, ttl, rubrics
_TABLE: Final = (
    (PRICE, 1, ("price", "ta"), LANE_TEMPLATE, 0, 0, 20, MONEY_RUBRICS),
    (MARKET, 1, ("market",), LANE_TEMPLATE, 0, 0, 60, MONEY_RUBRICS),
    (TOKEN_RISK, 3, ("scan",), LANE_FAST, 320, 900, 300, RISK_RUBRICS),
    (WALLET, 3, ("trace",), LANE_FAST, 320, 1000, 600, RISK_RUBRICS),
    (PORTFOLIO, 2, (), LANE_FAST, 300, 800, 60, CITE_RUBRICS + MONEY_RUBRICS[1:]),
    (HOWTO, 2, (), LANE_FAST, 380, 1200, 86400, CITE_RUBRICS),
    (EXPLOIT, 4, ("news",), LANE_DEEP, 700, 2400, 900, RISK_RUBRICS),
    (UNKNOWN, 2, (), LANE_FAST, 300, 800, 0, CITE_RUBRICS + MONEY_RUBRICS[1:]),
)

_PLANS: Final = {row[0]: SkillPlan(*row) for row in _TABLE}


def route_skill(message: str) -> SkillPlan:
    """Classify *message* into an answer plan. Pure; never raises.

    Empty input routes to ``unknown`` — the refusal path, never a data tool that
    would then be asked to answer nothing.
    """
    text = (message or "").strip()
    if not text:
        return _PLANS[UNKNOWN]

    address = bool(sig.EVM_RE.search(text) or sig.SOL_RE.search(text))
    pricey = bool(sig.PRICE_RE.search(text) or sig.SYMBOL_RE.search(text))

    if sig.PORTFOLIO_RE.search(text):
        return _PLANS[PORTFOLIO]
    if sig.WALLET_RE.search(text) or (address and sig.INTERROGATIVE_RE.search(text)):
        return _PLANS[WALLET]
    if sig.EXPLOIT_RE.search(text):
        return _PLANS[EXPLOIT]
    if address:
        return _PLANS[TOKEN_RISK]
    if sig.DEFINITION_RE.search(text) and not pricey:
        return _PLANS[HOWTO]
    if sig.RISK_RE.search(text):
        return _PLANS[TOKEN_RISK]
    if sig.MARKET_RE.search(text):
        return _PLANS[MARKET]
    if pricey:
        return _PLANS[PRICE]
    if sig.HOWTO_RE.search(text):
        return _PLANS[HOWTO]
    return _PLANS[UNKNOWN]


def estimate_cost(plan: SkillPlan) -> dict[str, Any]:
    """Token + dollar estimate for one answer of *plan*. Pure arithmetic.

    ``cost_usd`` is the real marginal cost (every selectable lane is local);
    ``paid_equivalent_usd`` prices the same tokens at a frontier API so the
    saving is visible. A ``template`` plan reports zeros — counting an evidence
    block a card never prompts with would overstate the card.
    """
    if not plan.uses_model:
        return {
            "intent": plan.intent,
            "lane": plan.lane,
            "model": plan.model,
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_total": 0,
            "cost_usd": 0.0,
            "paid_equivalent_usd": 0.0,
        }
    tokens_in = PROMPT_OVERHEAD_TOKENS + plan.retrieval_tokens
    tokens_out = plan.max_tokens
    paid = (tokens_in * REF_IN_USD_PER_MTOK + tokens_out * REF_OUT_USD_PER_MTOK) / 1_000_000
    return {
        "intent": plan.intent,
        "lane": plan.lane,
        "model": plan.model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "cost_usd": 0.0,
        "paid_equivalent_usd": round(paid, 6),
    }
