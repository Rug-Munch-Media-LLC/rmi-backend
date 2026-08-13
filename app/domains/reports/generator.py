"""T29 Research Report Generator.

Per v4.0 §T29. Given a token or wallet, compose a Markdown report
from every data source, sold via x402 at $5/report.

Sections (parallel-composable):
  - executive_summary  (LLM)
  - onchain            (catalog + RAG)
  - deployer           (Neo4j + reputation)
  - news_sentiment     (news_items + LLM summary)
  - rag_findings       (RAG engine)
  - social_signals     (placeholder v1)
  - risk_assessment    (deterministic, from catalog.reputation weights)
  - recommendation      (LLM, based on all sections)

Deterministic risk score (no LLM). LLM only for narrative text.
Falls back to templated content if LiteLLM is unreachable.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any
from uuid import uuid4

from app.catalog.llm_router import LLMRouter
from app.catalog.models import (
    RiskTier,
    ScanReport,
    utcnow,
)
from app.core.logging import get_logger
from app.domains.reports.citation_validator import validate_section

log = get_logger(__name__)

# ── Section prompts (v4.0 §T29) ─────────────────────────────────────
REPORT_PROMPTS: dict[str, str] = {
    "executive_summary": """You are an analyst at RugMunch Intelligence, a crypto scam-detection platform.
Write a 2-3 paragraph executive summary for a research report on this asset.

Subject type: {subject_type}
Subject ID: {subject_id}
Risk score: {risk_score}/100 ({risk_tier})
Key risk factors: {risk_factors}

Be concise. An analyst should be able to read this in 30 seconds and decide whether to dig deeper.
Use plain English. No hedging. State the verdict clearly.""",
    "onchain": """Write a 2-paragraph on-chain analysis for:

Subject: {subject_id}
Data: {data}

Cover: deployment, holders, liquidity, volume, contract characteristics.
If data is missing, say so explicitly. No speculation.""",
    "deployer": """Write a 2-paragraph deployer analysis for:

Deployer wallet: {deployer}
Reputation: {reputation_score}/100
Rug count: {rug_count}
Prior deployments: {deployments}

Cover: track record, prior rugs, longevity, news signals. Verdict on whether the deployer is trustworthy.""",
    "news_sentiment": """Write a 1-paragraph news sentiment summary for:

Subject: {subject_id}
Recent news count: {news_count}
Average sentiment: {avg_sentiment}
Top headline: {top_headline}

Verdict: bullish, bearish, or risk-elevating.""",
    "rag_findings": """Write a 1-paragraph RAG findings summary for:

Subject: {subject_id}
Findings: {findings}

Focus on the highest-confidence cross-references between news, on-chain, and social.""",
    "social_signals": """Write a 1-paragraph social signals summary for:

Subject: {subject_id}
Twitter mentions: {twitter_mentions}
Telegram groups: {telegram_groups}
Discord present: {discord_present}

Verdict on community strength and authenticity.""",
    "recommendation": """Based on the full report:

Subject: {subject_id}
Risk score: {risk_score}/100 ({risk_tier})
Top factors: {risk_factors}

Write a 1-paragraph RECOMMENDATION. Be direct: AVOID / CAUTION / NEUTRAL / OPPORTUNITY.
Justify in 2 sentences. If the asset is a serial rugger, say so clearly.""",
}


# ── Data gathering (fan-out from catalog) ─────────────────────────
async def _gather_token(catalog, chain: str, address: str) -> dict[str, Any]:
    """Gather all data sources for a token."""
    from app.catalog.models import Chain

    try:
        c = Chain(chain)
    except ValueError:
        return {"error": f"unknown chain: {chain}"}
    token_id = f"{chain}:{address}"
    token, deployer, news, rag_findings, _risk = await asyncio.gather(
        catalog.get_token(c, address),
        catalog.get_wallet(c, address) if False else asyncio.sleep(0, result=None),  # placeholder
        _fetch_news(catalog, token_id, since_hours=720),
        catalog.rag_search(query=token_id, collection="scam_intel", top_k=10),
        catalog.get_token_risk(c, address),
    )
    deployer = None
    if token and token.deployer_wallet_id:
        with contextlib.suppress(Exception):
            deployer = await catalog.get_wallet_by_id(token.deployer_wallet_id)
    return {
        "token": token,
        "deployer": deployer,
        "news": news,
        "rag_findings": rag_findings,
        "risk": _risk,
    }


async def _gather_wallet(catalog, chain: str, address: str) -> dict[str, Any]:
    """Gather data for a wallet report."""
    from app.catalog.models import Chain

    try:
        c = Chain(chain)
    except ValueError:
        return {"error": f"unknown chain: {chain}"}
    wallet_id = f"{chain}:{address}"
    wallet, news, rag_findings, entity = await asyncio.gather(
        catalog.get_wallet(c, address),
        _fetch_news(catalog, wallet_id, since_hours=720),
        catalog.rag_search(query=wallet_id, collection="wallet_labels", top_k=10),
        catalog.resolve_entity(wallet_id),
    )
    return {
        "wallet": wallet,
        "news": news,
        "rag_findings": rag_findings,
        "entity": entity,
    }


async def _fetch_news(catalog, subject_id: str, since_hours: int = 720) -> list[Any]:
    """Fetch news mentioning this subject."""
    if not catalog._health.postgres:
        return []
    try:
        async with catalog._pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT news_id, title, summary, source, published_at, sentiment_score
                   FROM news_items
                   WHERE $1 = ANY(tokens_mentioned)
                      OR $1 = ANY(wallets_mentioned)
                      OR title ILIKE $2
                   ORDER BY published_at DESC LIMIT 20""",
                subject_id,
                f"%{subject_id.split(':')[-1][:8]}%",
            )
        from app.domains.news.router import _adapt_legacy_row as _adapt_news_row

        return [_adapt_news_row(dict(r)) for r in rows]
    except (KeyError, ValueError, TypeError) as e:
        log.warning(f"fetch_news_fail: {e}")
        return []


# ── Risk scoring (deterministic) ───────────────────────────────────
def _compute_risk_token(token_data: dict[str, Any]) -> tuple[int, list[str], RiskTier]:
    """Deterministic 0-100 risk score from token data."""
    score = 0
    factors = []
    token = token_data.get("token")
    deployer = token_data.get("deployer")
    if token:
        if token.is_honeypot:
            score += 50
            factors.append("honeypot")
        if token.is_mintable:
            score += 20
            factors.append("mintable")
        if token.is_proxy:
            score += 10
            factors.append("proxy")
        if token.tax_buy_bps and token.tax_buy_bps > 1000:  # >10%
            score += 15
            factors.append(f"high_buy_tax_{token.tax_buy_bps}bps")
        if token.tax_sell_bps and token.tax_sell_bps > 1000:
            score += 15
            factors.append(f"high_sell_tax_{token.tax_sell_bps}bps")
        if token.risk_factors:
            score += min(len(token.risk_factors) * 5, 25)
    if deployer and hasattr(deployer, "rug_count"):
        if deployer.rug_count > 0:
            score += 30 * min(deployer.rug_count, 3)
            factors.append(f"deployer_{deployer.rug_count}_prior_rugs")
        if deployer.reputation_score and deployer.reputation_score < 30:
            score += 20
            factors.append("low_deployer_reputation")
    news = token_data.get("news", [])
    if news:
        bearish = [n for n in news if (n.sentiment_score or 0) < -0.3]
        if bearish:
            score += 15
            factors.append(f"bearish_news_{len(bearish)}")
    score = min(score, 100)
    if score < 25:
        tier = RiskTier.LOW
    elif score < 50:
        tier = RiskTier.MEDIUM
    elif score < 75:
        tier = RiskTier.HIGH
    else:
        tier = RiskTier.CRITICAL
    return score, factors, tier


def _compute_risk_wallet(wallet_data: dict[str, Any]) -> tuple[int, list[str], RiskTier]:
    score = 0
    factors = []
    wallet = wallet_data.get("wallet")
    entity = wallet_data.get("entity", {})
    if entity and entity.get("wallets") and len(entity["wallets"]) > 2:
        score += 15
        factors.append(f"cross_chain_{len(entity['wallets'])}")
    if wallet and wallet.is_suspicious:
        score += 30
        factors.append("flagged_suspicious")
    if wallet and wallet.tx_count > 10000:
        score += 10
        factors.append("high_tx_volume")
    news = wallet_data.get("news", [])
    bearish = [n for n in news if (n.sentiment_score or 0) < -0.3]
    if bearish:
        score += 15
        factors.append(f"bearish_news_{len(bearish)}")
    score = min(score, 100)
    if score < 25:
        tier = RiskTier.LOW
    elif score < 50:
        tier = RiskTier.MEDIUM
    elif score < 75:
        tier = RiskTier.HIGH
    else:
        tier = RiskTier.CRITICAL
    return score, factors, tier


# ── Report generation ──────────────────────────────────────────────
async def generate_token_report(catalog, chain: str, address: str, model: str = "deepseek-v3") -> ScanReport:
    """Generate a research report for a token. Falls back to templated
    sections if LLM is unreachable."""
    start = time.monotonic()
    data = await _gather_token(catalog, chain, address)
    if "error" in data:
        raise ValueError(data["error"])
    risk_score, risk_factors, risk_tier = _compute_risk_token(data)
    risk_factors_str = ", ".join(risk_factors) if risk_factors else "none detected"
    token = data.get("token")
    deployer = data.get("deployer")
    news = data.get("news", [])
    rag = data.get("rag_findings", [])

    avg_sent = sum(n.sentiment_score or 0 for n in news) / len(news) if news else 0
    top_headline = news[0].title if news else "no recent news"

    sections_ctx: dict[str, dict[str, Any]] = {
        "executive_summary": {
            "subject_type": "token",
            "subject_id": f"{chain}:{address}",
            "risk_score": risk_score,
            "risk_tier": risk_tier.value,
            "risk_factors": risk_factors_str,
        },
        "onchain": {
            "subject_id": f"{chain}:{address}",
            "data": (
                f"Symbol={token.symbol if token else '?'}, "
                f"Decimals={token.decimals if token else '?'}, "
                f"Deployed={token.deployed_at.isoformat() if token else '?'}, "
                f"honeypot={token.is_honeypot if token else '?'}, "
                f"mintable={token.is_mintable if token else '?'}, "
                f"tax_buy={token.tax_buy_bps if token else '?'}bps, "
                f"tax_sell={token.tax_sell_bps if token else '?'}bps"
            ),
        },
        "deployer": {
            "deployer": deployer.wallet_id if deployer else "unknown",
            "reputation_score": deployer.reputation_score if deployer else 50,
            "rug_count": deployer.rug_count if deployer else 0,
            "deployments": len(deployer.deployments) if deployer else 0,
        },
        "news_sentiment": {
            "subject_id": f"{chain}:{address}",
            "news_count": len(news),
            "avg_sentiment": f"{avg_sent:.2f}",
            "top_headline": top_headline,
        },
        "rag_findings": {
            "subject_id": f"{chain}:{address}",
            "findings": [r.get("text", "")[:200] for r in rag[:5]],
        },
        "social_signals": {
            "subject_id": f"{chain}:{address}",
            "twitter_mentions": 0,
            "telegram_groups": 0,
            "discord_present": False,
        },
        "recommendation": {
            "subject_id": f"{chain}:{address}",
            "risk_score": risk_score,
            "risk_tier": risk_tier.value,
            "risk_factors": risk_factors_str,
        },
    }

    # Run LLM sections in parallel
    llm = LLMRouter()

    async def _section(name: str, prompt: str) -> str:
        try:
            r = await llm.chat(prompt, model=model, max_tokens=400)
            return r if r else _template_fallback(name, sections_ctx[name])
        except (KeyError, TypeError) as e:
            log.warning(f"section_{name}_llm_fail: {e}")
            return _template_fallback(name, sections_ctx[name])

    tasks = [_section(name, REPORT_PROMPTS[name].format(**ctx)) for name, ctx in sections_ctx.items()]
    section_texts = await asyncio.gather(*tasks)
    sections = dict(zip(sections_ctx.keys(), section_texts, strict=False))

    # RAG-grounded validation: verify claims cite real sources
    # Only validate rag_findings and sections that should be grounded in RAG
    rag_sources = [r.get("text", "") for r in rag[:10]]  # Top 10 RAG chunks as sources
    validated_sections = dict(sections)  # Copy for validation

    if rag_sources:
        # Validate rag_findings section against RAG sources
        if "rag_findings" in validated_sections:
            rag_findings = validated_sections["rag_findings"]
            result = validate_section(rag_findings, rag_sources, on_unciteable="strip")
            validated_sections["rag_findings"] = result["validated_text"]
            log.info(
                "rag_findings_validated validation_rate=%.2f unciteable=%d",
                result["validation_rate"],
                result["unciteable_count"],
            )

        # Validate executive_summary and recommendation if they mention RAG findings
        for section_name in ["executive_summary", "recommendation"]:
            if section_name in validated_sections:
                section_text = validated_sections[section_name]
                # Only validate if section has citations [N]
                if "[" in section_text and "]" in section_text:
                    result = validate_section(section_text, rag_sources, on_unciteable="strip")
                    validated_sections[section_name] = result["validated_text"]
                    if result["unciteable_count"] > 0:
                        log.info(
                            "%s_validated unciteable=%d validation_rate=%.2f",
                            section_name,
                            result["unciteable_count"],
                            result["validation_rate"],
                        )

    sections = validated_sections

    # Build report
    report_id = uuid4().hex
    subject_id = f"{chain}:{address}"
    report = ScanReport(
        report_id=report_id,
        subject_type="token",
        subject_id=subject_id,
        generated_at=utcnow(),
        generated_by_model=model,
        risk_score=risk_score,
        risk_tier=risk_tier,
        sections=sections,
    )
    log.info(
        "report_generated type=token subject=%s risk=%d factors=%d took_ms=%d",
        subject_id,
        risk_score,
        len(risk_factors),
        int((time.monotonic() - start) * 1000),
    )
    return report


async def generate_wallet_report(catalog, chain: str, address: str, model: str = "deepseek-v3") -> ScanReport:
    """Generate a research report for a wallet."""
    data = await _gather_wallet(catalog, chain, address)
    if "error" in data:
        raise ValueError(data["error"])
    risk_score, risk_factors, risk_tier = _compute_risk_wallet(data)
    risk_factors_str = ", ".join(risk_factors) if risk_factors else "none detected"
    news = data.get("news", [])
    rag = data.get("rag_findings", [])
    avg_sent = sum(n.sentiment_score or 0 for n in news) / len(news) if news else 0

    sections_ctx: dict[str, dict[str, Any]] = {
        "executive_summary": {
            "subject_type": "wallet",
            "subject_id": f"{chain}:{address}",
            "risk_score": risk_score,
            "risk_tier": risk_tier.value,
            "risk_factors": risk_factors_str,
        },
        "onchain": {
            "subject_id": f"{chain}:{address}",
            "data": f"tx_count={data.get('wallet').tx_count if data.get('wallet') else '?'}, "  # type: ignore[union-attr]  # PTB None-able fields
            f"is_known_exchange={data.get('wallet').is_known_exchange if data.get('wallet') else '?'}",  # type: ignore[union-attr]  # PTB None-able fields
        },
        "deployer": {
            "deployer": "n/a (wallet report)",
            "reputation_score": 50,
            "rug_count": 0,
            "deployments": 0,
        },
        "news_sentiment": {
            "subject_id": f"{chain}:{address}",
            "news_count": len(news),
            "avg_sentiment": f"{avg_sent:.2f}",
            "top_headline": news[0].title if news else "no recent news",
        },
        "rag_findings": {
            "subject_id": f"{chain}:{address}",
            "findings": [r.get("text", "")[:200] for r in rag[:5]],
        },
        "social_signals": {
            "subject_id": f"{chain}:{address}",
            "twitter_mentions": 0,
            "telegram_groups": 0,
            "discord_present": False,
        },
        "recommendation": {
            "subject_id": f"{chain}:{address}",
            "risk_score": risk_score,
            "risk_tier": risk_tier.value,
            "risk_factors": risk_factors_str,
        },
    }

    llm = LLMRouter()

    async def _section(name, prompt) -> Any:
        try:
            r = await llm.chat(prompt, model=model, max_tokens=400)
            return r if r else _template_fallback(name, sections_ctx[name])
        except (ConnectionError, OSError, TimeoutError):
            return _template_fallback(name, sections_ctx[name])

    tasks = [_section(n, REPORT_PROMPTS[n].format(**ctx)) for n, ctx in sections_ctx.items()]
    section_texts = await asyncio.gather(*tasks)
    sections = dict(zip(sections_ctx.keys(), section_texts, strict=False))

    # RAG-grounded validation (same as token reports)
    rag_sources = [r.get("text", "") for r in rag[:10]]
    validated_sections = dict(sections)

    if rag_sources:
        if "rag_findings" in validated_sections:
            rag_findings = validated_sections["rag_findings"]
            result = validate_section(rag_findings, rag_sources, on_unciteable="strip")
            validated_sections["rag_findings"] = result["validated_text"]
            log.info(
                "rag_findings_validated validation_rate=%.2f unciteable=%d",
                result["validation_rate"],
                result["unciteable_count"],
            )

        for section_name in ["executive_summary", "recommendation"]:
            if section_name in validated_sections:
                section_text = validated_sections[section_name]
                if "[" in section_text and "]" in section_text:
                    result = validate_section(section_text, rag_sources, on_unciteable="strip")
                    validated_sections[section_name] = result["validated_text"]
                    if result["unciteable_count"] > 0:
                        log.info(
                            "%s_validated unciteable=%d validation_rate=%.2f",
                            section_name,
                            result["unciteable_count"],
                            result["validation_rate"],
                        )

    sections = validated_sections

    report_id = uuid4().hex
    subject_id = f"{chain}:{address}"
    return ScanReport(
        report_id=report_id,
        subject_type="wallet",
        subject_id=subject_id,
        generated_at=utcnow(),
        generated_by_model=model,
        risk_score=risk_score,
        risk_tier=risk_tier,
        sections=sections,
    )


def _template_fallback(name: str, ctx: dict[str, Any]) -> str:
    """Templated content for when LLM is unreachable."""
    sid = ctx.get("subject_id", "unknown")
    rs = ctx.get("risk_score", "?")
    rt = ctx.get("risk_tier", "?")
    rf = ctx.get("risk_factors", "n/a")
    if name == "executive_summary":
        return (
            f"## Executive Summary\n\n"
            f"Subject {sid} has a risk score of {rs}/100 (tier: {rt}). "
            f"Key risk factors: {rf}. "
            f"This is a templated fallback (LLM unavailable). For full analysis, ensure LiteLLM is reachable."
        )
    if name == "onchain":
        return f"## On-Chain Activity\n\n{ctx.get('data', 'no data')}"
    if name == "deployer":
        return f"## Deployer Analysis\n\nDeployer: {ctx.get('deployer', 'unknown')}\nReputation: {ctx.get('reputation_score', '?')}/100"
    if name == "news_sentiment":
        return f"## News Sentiment\n\n{ctx.get('news_count', 0)} recent articles. Avg sentiment: {ctx.get('avg_sentiment', 0)}"
    if name == "rag_findings":
        return f"## RAG Findings\n\n{len(ctx.get('findings', []))} findings (templated)"
    if name == "social_signals":
        return "## Social Signals\n\nTemplated (no real data)"
    if name == "recommendation":
        verdict = "AVOID" if rs >= 75 else "CAUTION" if rs >= 50 else "NEUTRAL" if rs >= 25 else "OPPORTUNITY"
        return f"## Recommendation\n\n**{verdict}** (risk {rs}/100). Templated fallback."
    return f"## {name.title()}\n\n(Templated fallback)"


# ── Save to Postgres + MinIO ────────────────────────────────────────
async def save_report(catalog, report: ScanReport) -> bool:
    """Persist report metadata to Postgres + markdown to MinIO."""
    if not catalog._health.postgres:
        return False
    try:
        async with catalog._pg_pool.acquire() as conn:
            import json as _json

            await conn.execute(
                """INSERT INTO scan_reports
                   (report_id, subject_type, subject_id, generated_at, generated_by_model,
                    risk_score, risk_tier, sections, markdown_url, paid_via_x402)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   ON CONFLICT (report_id) DO UPDATE SET
                     sections=EXCLUDED.sections,
                     risk_score=EXCLUDED.risk_score,
                     risk_tier=EXCLUDED.risk_tier""",
                report.report_id,
                report.subject_type,
                report.subject_id,
                report.generated_at,
                report.generated_by_model,
                report.risk_score,
                report.risk_tier.value,
                _json.dumps(report.sections),
                str(report.markdown_url) if report.markdown_url else None,
                report.paid_via_x402,
            )
        # Try MinIO upload (graceful if not available)
        if catalog._health.minio:
            try:
                # MinIO upload is complex; skip for v1, store markdown in Postgres instead
                # Future: use httpx PUT to minio with signed URL
                pass
            except Exception as e:
                log.debug(f"minio_upload_skip: {e}")
        return True
    except Exception as e:
        log.warning(f"save_report_fail: {e}")
        return False
