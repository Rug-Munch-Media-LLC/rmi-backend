"""AI scan-verdict synthesis (2026-08-26).

Feeds ALL engine outputs + indexer-backed data through the LLM (the
shared llm_config fallback chain) together with OUR scoring matrix, and
returns a narrative verdict deeper than a human could produce from the
raw numbers. Fail-open: returns None on any error — cards render
without the section.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("scan_verdict_ai")

_SCORING_MATRIX = """Scoring matrix (RugMunch internal):
- Honeypot or blocked sells = automatic CRITICAL.
- Mint authority live = +30; freeze authority = +25; owner not renounced = +15.
- Deployer track record: each prior rugged/scam launch = +20 (serial deployer).
- Bundle: >30% same-block buyers = +25; deployer-funded wallets = +20.
- First buyers: >50% fresh wallets (<24h) = +15; clustered funding = +15.
- LP: unlocked or <50% locked = +20; liquidity pull events = +30.
- Holder concentration: top10 >60% = +15; fast concentration velocity = +10.
- Fake/wash volume suspicion = +10. Verified contract = -5. Renounced = -10.
Bands: 0-39 LOW, 40-59 MEDIUM, 60-79 HIGH, 80+ CRITICAL."""


def _compact_signals(r: dict[str, Any]) -> str:
    """Compact the scan payload into the signals that matter."""
    bits: list[str] = []
    if r.get("risk_score") is not None:
        bits.append(f"rule_engine_score={r['risk_score']}")
    for k in ("honeypot", "mintable", "owner_renounced", "contract_verified"):
        if r.get(k) is not None:
            bits.append(f"{k}={r[k]}")
    dp = r.get("deployer_profile") or {}
    if isinstance(dp, dict):
        prior = dp.get("previous_tokens_launched") or dp.get("total_launches") or dp.get("prior_launches")
        scam = dp.get("previous_scam_tokens") or dp.get("rugged_count") or dp.get("scam_related_count")
        if prior:
            bits.append(f"deployer_prior_launches={prior} scams={scam}")
    ba = r.get("bundle_analysis") or {}
    if isinstance(ba, dict) and ba.get("total_bundled_percentage") is not None:
        bits.append(f"bundled_pct={ba.get('total_bundled_percentage')}")
    fb = r.get("first_buyers") or {}
    if isinstance(fb, dict):
        agg = fb.get("aggregates") or {}
        if agg.get("fresh_wallet_pct") is not None:
            bits.append(
                f"first_buyers={agg.get('unique_buyers')} "
                f"fresh_pct={agg.get('fresh_wallet_pct')} "
                f"bundled_pct={agg.get('bundled_pct')}"
            )
    lp = r.get("lp_lock_pct") or (r.get("token_metrics") or {}).get("lp_locked_pct")
    if lp is not None:
        bits.append(f"lp_locked_pct={lp}")
    wm = r.get("wallet_metrics") or {}
    if isinstance(wm, dict) and wm.get("top10_holder_percent") is not None:
        bits.append(f"top10_holder_pct={wm.get('top10_holder_percent')}")
    return "; ".join(bits) if bits else "no signals"


async def synthesize_scan_verdict(r: dict[str, Any]) -> str | None:
    """Narrative AI verdict over engine outputs + indexer data.

    Returns None when LLM keys are absent or generation fails — callers
    render cards without the section (never block a scan on AI).
    """
    try:
        from app.llm_config import generate_content
    except Exception:
        return None

    token = f"{r.get('name', '?')} ({r.get('symbol', '?')}) on {r.get('chain', '?')}"
    prompt = f"""You are RugMunch's senior on-chain forensics analyst. Analyze this token
scan against the scoring matrix and write a verdict a degen can act on.

Token: {token}
Signals: {_compact_signals(r)}

{_SCORING_MATRIX}

Write EXACTLY this shape, plain text, no markdown headers:
VERDICT: <LOW|MEDIUM|HIGH|CRITICAL> — <one-line bottom line>
WHY: <3-4 bullets of the strongest evidence, each with its matrix weight>
DEPLOYER: <what the track record means here, 1-2 sentences>
WATCH: <the single behavior to monitor if they ape in, 1 sentence>
Keep it under 120 words total. No hedging filler."""

    try:
        out = await generate_content(
            prompt,
            system="You are a precise crypto forensics analyst. Evidence-weighted, no hype.",
            max_tokens=400,
            temperature=0.3,
        )
        return (out or "").strip() or None
    except Exception as exc:
        logger.debug("ai_verdict_failed: %s", str(exc)[:120])
        return None
