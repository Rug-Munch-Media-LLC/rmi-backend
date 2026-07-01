"""T29 Research Report Generator — HTTP routes.

Per v4.0 §T29. POST /api/v1/reports/generate composes a research report
from every data source, sold via x402 at $5/report.

Pricing tiers (v4.0):
  Single report: $5
  Bulk batch 20: $50 (bulk discount)
  Subscription: $500/mo (unlimited)

x402 payment gate is enforced by the middleware in app/domain/x402/middleware.py
when an X-Payment header is required. For the open-source public preview, the
endpoint is callable without payment but the response includes paid_via_x402=null
so the caller can decide whether to integrate the payment flow.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.catalog.service import get_catalog
from app.domain.reports.generator import (
    generate_token_report,
    generate_wallet_report,
    save_report,
)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


class GenerateRequest(BaseModel):
    subject_type: str = Field(..., pattern="^(token|wallet)$")
    subject_id: str = Field(..., description='"chain:address"')
    model: str = "deepseek-v3"
    save: bool = True


class GenerateResponse(BaseModel):
    report_id: str
    subject_type: str
    subject_id: str
    risk_score: int
    risk_tier: str
    risk_factors: list[str] = Field(default_factory=list)
    generated_by_model: str
    generated_at: str
    sections: dict[str, str] = Field(default_factory=dict)
    markdown: str
    paid_via_x402: str | None = None
    error: str | None = None


@router.post("/generate", response_model=GenerateResponse)
async def generate_report(req: GenerateRequest) -> GenerateResponse:
    """Generate a research report for a token or wallet.

    Composes 7 sections in parallel via LiteLLM. Falls back to templated
    content if LLM is unreachable. Saves to Postgres on success.
    """
    catalog = get_catalog()
    await catalog._init_stores()
    if ":" not in req.subject_id:
        raise HTTPException(400, "subject_id must be 'chain:address'")
    chain, address = req.subject_id.split(":", 1)
    try:
        if req.subject_type == "token":
            report = await generate_token_report(catalog, chain, address, model=req.model)
        else:
            report = await generate_wallet_report(catalog, chain, address, model=req.model)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"report_generation_failed: {e}")
    if req.save:
        await save_report(catalog, report)
    # Derive risk_factors from sections (parse them back if needed)
    risk_factors = _extract_risk_factors(report.sections.get("executive_summary", ""))
    return GenerateResponse(
        report_id=report.report_id,
        subject_type=report.subject_type,
        subject_id=report.subject_id,
        risk_score=report.risk_score,
        risk_tier=report.risk_tier.value,
        risk_factors=risk_factors,
        generated_by_model=report.generated_by_model,
        generated_at=report.generated_at.isoformat(),
        sections=report.sections,
        markdown=report.to_markdown(),
        paid_via_x402=report.paid_via_x402,
    )


def _extract_risk_factors(exec_summary: str) -> list[str]:
    """Heuristically extract risk factor names from the exec summary."""
    if not exec_summary:
        return []
    keywords = [
        "honeypot",
        "mintable",
        "proxy",
        "high_buy_tax",
        "high_sell_tax",
        "deployer_rugs",
        "low_deployer_reputation",
        "bearish_news",
        "cross_chain",
        "flagged_suspicious",
        "high_tx_volume",
    ]
    text_l = exec_summary.lower()
    return [k for k in keywords if k in text_l]


@router.get("/{report_id}")
async def get_report(report_id: str) -> dict:
    """Retrieve a previously generated report from Postgres."""
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    try:
        import json as _json

        async with catalog._pg_pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM scan_reports WHERE report_id=$1", report_id)
        if not r:
            raise HTTPException(404, "report not found")
        d = dict(r)
        if isinstance(d.get("sections"), str):
            d["sections"] = _json.loads(d["sections"])
        d["generated_at"] = d["generated_at"].isoformat()
        return d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"get_report_fail: {e}")
