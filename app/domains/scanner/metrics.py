"""
Scanner Data Source Metrics — Own-Data Coverage Tracking
===========================================================
Tracks what percentage of scanner questions are answered by own data
vs external API fallbacks. Exposes a /metrics endpoint for monitoring.

Usage:
    from app.domains.scanner.metrics import record_check_source, get_coverage_report

    # After each scan check:
    record_check_source("own_data")  # Answered from ClickHouse
    record_check_source("databus")   # Answered by DataBus free tier
    record_check_source("external")  # Required external API call

    # Get report:
    report = get_coverage_report()
    # { "own_data_pct": 73.5, "databus_pct": 21.2, "external_pct": 5.3, ... }
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

# ── SCANNER_METRICS ──────────────────────────────────────────────────────
# Global counter: tracks the data source for every scanner check performed.
# Sources:
#   "own_data"  — Answered from own ClickHouse data (zero cost)
#   "databus"   — Answered via DataBus free tier provider chain
#   "external"  — Required external API call (DexScreener, GoPlus, CoinGecko, etc.)
#
# Per-module breakdown allows identifying which scanner modules most need
# own-data conversion.

_SCANNER_METRICS: dict[str, int] = defaultdict(int)
_SCANNER_MODULE_METRICS: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_OWN_DATA_QUESTIONS: dict[str, str] = {}
_START_TIME: float = time.time()


# ── Question registry — maps each scanner question to its data source ────
# Each scanner "question" (e.g., "What's the price?", "Is it a honeypot?")
# is mapped to the module that answers it.

SCANNER_QUESTIONS: dict[str, str] = {
    "price": "What's the price of this token?",
    "honeypot": "Is this token a honeypot?",
    "top_holders": "Who are the top holders?",
    "lp_lock": "Is the LP locked or burned?",
    "deployer": "Who deployed this token?",
    "volume": "What's the trading volume?",
    "bundle_buys": "Are there bundle buys at launch?",
    "copycat": "Is this a copycat token?",
    "holder_concentration": "Is holder distribution healthy?",
    "token_age": "How old is this token?",
    "contract_verified": "Is the contract verified?",
    "ownership_renounced": "Is ownership renounced?",
    "social_sentiment": "What's the social sentiment?",
    "buy_sell_tax": "What are buy/sell taxes?",
    "volume_authenticity": "Is the volume authentic?",
    "deployer_reputation": "What's the deployer reputation?",
    "rug_patterns": "Are there rug-pull patterns?",
    "wallet_address_labels": "What labels does this wallet have?",
    "wallet_risk_score": "What's the wallet risk score?",
    "funding_source": "Where did this wallet get funding?",
}


def record_check_source(source: str, module: str = "general") -> None:
    """Record that a scanner check was answered by a given data source.

    Args:
        source: "own_data", "databus", or "external"
        module: Scanner module name (e.g., "price", "honeypot", "holders")
    """
    if source not in ("own_data", "databus", "external"):
        source = "external"  # Default to external for unknown sources

    _SCANNER_METRICS[source] += 1
    _SCANNER_MODULE_METRICS[module][source] += 1


def record_question_source(question_key: str, source: str) -> None:
    """Record which data source answered a specific scanner question.

    Args:
        question_key: Key from SCANNER_QUESTIONS
        source: "own_data", "databus", or "external"
    """
    _OWN_DATA_QUESTIONS[question_key] = source
    record_check_source(source, question_key)


def get_coverage_report() -> dict[str, Any]:
    """Get the own-data coverage report.

    Returns:
        {
            "own_data_pct": float,    # Percentage answered by own data
            "databus_pct": float,     # Percentage answered by DataBus free tier
            "external_pct": float,    # Percentage requiring external API
            "total_checks": int,      # Total checks recorded
            "by_module": {...},       # Per-module breakdown
            "questions": {...},       # Per-question data source
            "coverage_by_tier": {...},  # FREE/PRO/ELITE coverage estimates
            "uptime_seconds": float,
        }
    """
    total = sum(_SCANNER_METRICS.values())
    own = _SCANNER_METRICS.get("own_data", 0)
    databus = _SCANNER_METRICS.get("databus", 0)
    external = _SCANNER_METRICS.get("external", 0)

    # Per-module breakdown
    modules_report = {}
    for mod, sources in sorted(_SCANNER_MODULE_METRICS.items()):
        mod_total = sum(sources.values())
        modules_report[mod] = {
            "total": mod_total,
            "own_data": sources.get("own_data", 0),
            "databus": sources.get("databus", 0),
            "external": sources.get("external", 0),
            "own_data_pct": round(sources.get("own_data", 0) / mod_total * 100, 1) if mod_total > 0 else 0,
        }

    # Per-question tracking
    question_sources = {q: _OWN_DATA_QUESTIONS.get(q, "unknown") for q in SCANNER_QUESTIONS}

    # Count how many total questions are answered by own data
    own_data_question_count = sum(1 for s in _OWN_DATA_QUESTIONS.values() if s == "own_data")
    total_questions_tracked = len(_OWN_DATA_QUESTIONS)

    return {
        "own_data_pct": round(own / total * 100, 1) if total > 0 else 0,
        "databus_pct": round(databus / total * 100, 1) if total > 0 else 0,
        "external_pct": round(external / total * 100, 1) if total > 0 else 0,
        "total_checks": total,
        "own_data_checks": own,
        "databus_checks": databus,
        "external_api_checks": external,
        "by_module": modules_report,
        "questions": question_sources,
        "questions_by_own_data": own_data_question_count,
        "total_questions_tracked": total_questions_tracked,
        "questions_own_data_pct": round(own_data_question_count / total_questions_tracked * 100, 1)
        if total_questions_tracked > 0
        else 0,
        "uptime_seconds": round(time.time() - _START_TIME, 1),
    }


def get_module_coverage(module: str) -> dict[str, Any]:
    """Get coverage report for a specific module."""
    sources = _SCANNER_MODULE_METRICS.get(module, {})
    total = sum(sources.values())
    return {
        "module": module,
        "total_checks": total,
        "own_data": sources.get("own_data", 0),
        "databus": sources.get("databus", 0),
        "external": sources.get("external", 0),
        "own_data_pct": round(sources.get("own_data", 0) / total * 100, 1) if total > 0 else 0,
    }


def get_top_external_modules(limit: int = 5) -> list[dict[str, Any]]:
    """Get modules that most need own-data conversion.

    Returns modules sorted by highest external API dependency.
    """
    modules = []
    for mod, sources in _SCANNER_MODULE_METRICS.items():
        total = sum(sources.values())
        external_pct = sources.get("external", 0) / total * 100 if total > 0 else 0
        if sources.get("external", 0) > 0:
            modules.append(
                {
                    "module": mod,
                    "external_count": sources.get("external", 0),
                    "external_pct": round(external_pct, 1),
                    "total": total,
                }
            )

    modules.sort(key=lambda x: x["external_count"], reverse=True)
    return modules[:limit]


def record_token_scan_coverage(
    data_sources: list[str],
    modules_run: list[dict[str, Any]],
) -> None:
    """Record coverage data from a completed token scan.

    Call this at the end of scan_token() to track which sources
    were used for each module.

    Args:
        data_sources: List of data source strings (e.g., ["own", "dexscreener", "goplus"])
        modules_run: List of module run dicts with module name and status
    """
    # Track per-module sources
    for mod_entry in modules_run:
        module_name = mod_entry.get("module", "unknown")
        status = mod_entry.get("status", "")
        result = mod_entry.get("result", {})

        if status == "success":
            if isinstance(result, dict):
                source = result.get("source", "")
                if "own" in str(source).lower() or "clickhouse" in str(source).lower():
                    record_check_source("own_data", module_name)
                elif (
                    "dexscreener" in str(source).lower()
                    or "goplus" in str(source).lower()
                    or "defillama" in str(source).lower()
                    or "coingecko" in str(source).lower()
                ):
                    record_check_source("external", module_name)
                elif "databus" in str(source).lower():
                    record_check_source("databus", module_name)
                else:
                    record_check_source("external", module_name)
            else:
                record_check_source("external", module_name)

    # Track overall data sources
    for src in data_sources:
        if "own" in src.lower() or "clickhouse" in src.lower():
            record_check_source("own_data", "overall")
        elif "databus" in src.lower():
            record_check_source("databus", "overall")
        else:
            record_check_source("external", "overall")


def reset_metrics() -> None:
    """Reset all metrics counters (for testing)."""
    _SCANNER_METRICS.clear()
    _SCANNER_MODULE_METRICS.clear()
    _OWN_DATA_QUESTIONS.clear()


# ── Question coverage constants ─────────────────────────────────────────

# Which scanner questions we aim to answer from own data
# (set to True when own-data engine is implemented)
OWN_DATA_CAPABILITY: dict[str, bool] = {
    "price": True,  # own_price_oracle
    "honeypot": True,  # own_security_analysis
    "top_holders": True,  # own_holder_analysis
    "lp_lock": True,  # own_lp_lock (NEW)
    "deployer": True,  # deployer_events in ClickHouse
    "volume": True,  # own_dex_data
    "bundle_buys": True,  # own_bundle_detect (NEW)
    "copycat": True,  # own_copycat_detect (NEW)
    "holder_concentration": True,  # own_holder_analysis
    "token_age": True,  # deployer_events → own_security_analysis
    "contract_verified": False,  # Still needs Sourcify/Explorer API
    "ownership_renounced": False,  # Still needs GoPlus/RPC for now
    "social_sentiment": False,  # No on-chain substitute
    "buy_sell_tax": False,  # Needs bytecode analysis
    "volume_authenticity": False,  # Still needs DexScreener for comparison
    "deployer_reputation": True,  # deployer_reputation engine
    "rug_patterns": True,  # chain_pattern_detector
    "wallet_address_labels": True,  # wallet_memory labels
    "wallet_risk_score": True,  # wallet_memory risk scoring
    "funding_source": True,  # wallet_memory funding trace
}

# Summary metrics
KNOWN_QUESTIONS = len(SCANNER_QUESTIONS)
OWN_DATA_QUESTIONS_COUNT = sum(1 for v in OWN_DATA_CAPABILITY.values() if v)
OWN_DATA_PCT_TARGET = round(OWN_DATA_QUESTIONS_COUNT / KNOWN_QUESTIONS * 100, 1) if KNOWN_QUESTIONS > 0 else 0
