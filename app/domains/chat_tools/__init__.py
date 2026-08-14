"""Chat tool-use layer — live data grounding for the RMI chat.

The chat LLM must ground price / scan / TA / market answers in real numbers
instead of hallucinating. This package provides:

- ``_detect_tool_intent`` — pure classifier over the user message.
- ``_exec_price`` / ``_exec_scan`` / ``_exec_trace`` / ``_exec_ta`` /
  ``_exec_market`` / ``_exec_news`` / ``_exec_suspicious`` /
  ``_exec_prediction`` / ``_exec_perp`` — best-effort async executors that
  call the DIRECT data modules (no x402 endpoints). Each is fail-open:
  returns ``None`` on any error/timeout and NEVER raises.
- ``_run_tool_for_intent`` — dispatcher with an 8s timeout.

Everything here is defensive by design: a failed executor is invisible to
the chat route (it simply falls through to RAG/LLM). ``chat.py`` remains the
integration point — see ``_fetch_tool_context`` there.

This package is the split of the former single ``chat_tools.py`` god file:
per-tool implementation lives in the ``price``/``scan``/``trace``/``ta``/
``market``/``news``/``suspicious``/``prediction``/``perp`` submodules, the
classifier in ``_intent``, shared helpers in ``_shared``, and the executor
registry in ``dispatcher``. This module is a thin re-export shim so every
existing importer keeps working against the SAME objects (no behavior change).
"""

from __future__ import annotations

from app.core.logging import get_logger

from ._intent import (
    _DEFINITION_QUESTION_RE,
    _FORENSICS_WORD_RE,
    _INTENT_MARKET_PHRASES,
    _INTENT_PERP_PHRASES,
    _INTENT_PREDICTION_PHRASES,
    _INTENT_PRICE_PHRASES,
    _INTENT_SCAN_KEYWORDS,
    _INTENT_SUSPICIOUS_PHRASES,
    _INTENT_TRACE_PHRASES,
    _LIST_REQUEST_RE,
    _NEWS_KEYWORD_RE,
    _SUS_STANDALONE_RE,
    _TA_KEYWORD_RE,
    _WILL_HAPPEN_RE,
    _detect_tool_intent,
    _is_suspicious_list_request,
)
from ._shared import (
    _EVM_ADDRESS_RE,
    _SOLANA_ADDRESS_RE,
    _SYMBOL_RE,
    _TOKEN_NAME_TO_SYMBOL,
    _chain_for_address,
    _extract_first_address,
    _extract_price_symbol,
    _to_float,
)
from .dispatcher import _EXECUTORS, TOOL_TIMEOUT_SECONDS, _run_tool_for_intent
from .market import _exec_market, _fetch_fear_greed
from .news import _NEWS_MAX_ITEMS, _NEWS_SCROLL_WINDOW, _exec_news, _relative_time
from .perp import _exec_perp
from .prediction import _exec_prediction
from .price import _COINGECKO_IDS, _best_dex_pair, _exec_price
from .scan import _exec_scan
from .suspicious import (
    _LAUNCH_ENTITY_KEYS,
    _SUSPICIOUS_ENRICH_MAX,
    _SUSPICIOUS_FRESH_LIMIT,
    _SUSPICIOUS_LAUNCH_LIMIT,
    _SUSPICIOUS_LIQUIDITY_LOW,
    _SUSPICIOUS_LIQUIDITY_THIN,
    _SUSPICIOUS_MAX_ROWS,
    _SUSPICIOUS_MC_TINY,
    _SUSPICIOUS_YOUNG_SECONDS,
    _exec_suspicious,
    _is_truthy,
    _launch_age_seconds,
    _launch_rug_risk,
    _launch_rug_signals,
    _launch_scammer_hit,
    _launch_suspicion_score,
    _merge_launches,
    _rank_suspicious,
    _render_suspicious,
    _rug_risk_segment,
    _suspicious_flags,
)
from .ta import _exec_ta, _inline_ta_text, _rsi
from .trace import _TRACE_TIMEOUT_SECONDS, _exec_trace, _render_graph_summary, _render_trace_summary

logger = get_logger(__name__)

# ── Optional domain hooks (kept module-level, as in the original module) ──
# These are patched by tests on app.domains.chat_tools.*; the executors
# resolve them through this shim at call time so the patches apply.

# Defensive import: the TA domain (app/domains/ta) is being built by a
# parallel agent. Until it lands, these stay None and _exec_ta falls back to
# an inline RSI + change summary.
try:
    from app.domains.ta.summary import ta_summary, ta_summary_text
except ImportError:  # pragma: no cover — parallel agent may not have landed
    ta_summary = None  # type: ignore[assignment]
    ta_summary_text = None  # type: ignore[assignment]

# Defensive import: the consolidated rug-risk scorer
# (app/domains/scanner/rug_risk_score) is a pure 0-100 scorer; when it is
# unavailable (or mid-refactor and not importable), _exec_suspicious falls
# back to the legacy _launch_suspicion_score. Broad except: a broken module
# (e.g. SyntaxError while being edited) must never break the chat tool layer.
try:
    from app.domains.scanner.rug_risk_score import risk_verdict_text, rug_risk_score
except Exception:  # defensive: scorer availability is optional
    rug_risk_score = None  # type: ignore[assignment]
    risk_verdict_text = None  # type: ignore[assignment]

# Explicit re-exports for the former module surface. Listed in ``__all__`` so
# mypy (strict, no-implicit-reexport) and ruff (F401) treat every name as an
# intentional export and importers keep working against the SAME objects.
__all__ = [
    "TOOL_TIMEOUT_SECONDS",
    "_COINGECKO_IDS",
    "_DEFINITION_QUESTION_RE",
    "_EVM_ADDRESS_RE",
    "_EXECUTORS",
    "_FORENSICS_WORD_RE",
    "_INTENT_MARKET_PHRASES",
    "_INTENT_PERP_PHRASES",
    "_INTENT_PREDICTION_PHRASES",
    "_INTENT_PRICE_PHRASES",
    "_INTENT_SCAN_KEYWORDS",
    "_INTENT_SUSPICIOUS_PHRASES",
    "_INTENT_TRACE_PHRASES",
    "_LAUNCH_ENTITY_KEYS",
    "_LIST_REQUEST_RE",
    "_NEWS_KEYWORD_RE",
    "_NEWS_MAX_ITEMS",
    "_NEWS_SCROLL_WINDOW",
    "_SOLANA_ADDRESS_RE",
    "_SUSPICIOUS_ENRICH_MAX",
    "_SUSPICIOUS_FRESH_LIMIT",
    "_SUSPICIOUS_LAUNCH_LIMIT",
    "_SUSPICIOUS_LIQUIDITY_LOW",
    "_SUSPICIOUS_LIQUIDITY_THIN",
    "_SUSPICIOUS_MAX_ROWS",
    "_SUSPICIOUS_MC_TINY",
    "_SUSPICIOUS_YOUNG_SECONDS",
    "_SUS_STANDALONE_RE",
    "_SYMBOL_RE",
    "_TA_KEYWORD_RE",
    "_TOKEN_NAME_TO_SYMBOL",
    "_TRACE_TIMEOUT_SECONDS",
    "_WILL_HAPPEN_RE",
    "_best_dex_pair",
    "_chain_for_address",
    "_detect_tool_intent",
    "_exec_market",
    "_exec_news",
    "_exec_perp",
    "_exec_prediction",
    "_exec_price",
    "_exec_scan",
    "_exec_suspicious",
    "_exec_ta",
    "_exec_trace",
    "_extract_first_address",
    "_extract_price_symbol",
    "_fetch_fear_greed",
    "_inline_ta_text",
    "_is_suspicious_list_request",
    "_is_truthy",
    "_launch_age_seconds",
    "_launch_rug_risk",
    "_launch_rug_signals",
    "_launch_scammer_hit",
    "_launch_suspicion_score",
    "_merge_launches",
    "_rank_suspicious",
    "_relative_time",
    "_render_graph_summary",
    "_render_suspicious",
    "_render_trace_summary",
    "_rsi",
    "_rug_risk_segment",
    "_run_tool_for_intent",
    "_suspicious_flags",
    "_to_float",
    "risk_verdict_text",
    "rug_risk_score",
    "ta_summary",
    "ta_summary_text",
]
