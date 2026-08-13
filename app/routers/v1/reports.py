"""
V1 Custom Report Generation — POST /api/v1/reports/custom
===========================================================
Generate custom reports with user-selected sections.

Sections:
  - risk         → CatalogService.get_token_risk or wallet risk
  - holders      → TokenService.get_holders or ChainFeeder
  - transactions  → recent transaction history
  - news         → news sentiment
  - technical    → contract analysis

Graceful degradation:
  - Each section is fetched independently
  - If a section fails, its value is {"error": "unavailable"}
  - Never returns HTTP 500
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/reports", tags=["reports-v1"])

# ── Request / Response models ────────────────────────────────────────


class ReportRequest(BaseModel):
    """Custom report generation request."""

    subject_type: Literal["token", "wallet", "entity"]
    subject_id: str  # Address or token contract
    sections: list[Literal["risk", "holders", "transactions", "news", "technical"]] = Field(..., min_length=1)
    format: Literal["json", "markdown"] = "json"


# ── Section handlers ────────────────────────────────────────────────


async def _section_risk(subject_type: str, subject_id: str, chain: str) -> dict[str, Any]:
    """Fetch risk assessment section."""
    try:
        if subject_type == "token":
            # Try catalog service first (3-store composition)
            try:
                from app.catalog.models import Chain
                from app.catalog.service import get_catalog

                c = Chain(chain)
                catalog = get_catalog()
                return await catalog.get_token_risk(c, subject_id)
            except ImportError:
                logger.debug("Catalog service not available, trying token service fallback")
                # Fallback: try the domain token service
                try:
                    from app.domains.token.service import TokenService

                    svc = TokenService()
                    risk = await svc.get_risk(subject_id, chain)
                    return {
                        "score": risk.risk_score,
                        "tier": risk.risk_level.value if hasattr(risk.risk_level, "value") else str(risk.risk_level),
                        "factors": [f.get("code", "") for f in risk.flags] if hasattr(risk, "flags") else [],
                    }
                except ImportError:
                    logger.debug("TokenService not available")
                    return {"error": "unavailable", "note": "No risk service available"}
                except Exception as e:
                    logger.debug(f"TokenService risk failed: {e}")
                    return {"error": "unavailable", "note": "No risk service available"}

            except Exception as e:
                logger.debug(f"Catalog service risk failed: {e}")
                return {"error": "unavailable", "note": "No risk service available"}

        elif subject_type == "wallet":
            # Wallet risk via RiskScorer
            try:
                from app.wallet_memory.risk_scoring import RiskScorer

                scorer = RiskScorer()
                result = await scorer.score(subject_id, chain)
                return {
                    "score": result.get("score", 0),
                    "tier": result.get("level", "unknown"),
                    "components": result.get("components", {}),
                    "warnings": result.get("warnings", []),
                }
            except ImportError:
                logger.debug("RiskScorer not available")
                return {"error": "unavailable", "note": "Wallet risk service unavailable"}
            except Exception as e:
                logger.debug(f"RiskScorer failed: {e}")
                return {"error": "unavailable", "note": "Wallet risk service unavailable"}

        return {"error": "unavailable", "note": f"Risk not supported for {subject_type}"}
    except Exception as exc:
        logger.debug("section_risk_fail: %s", exc)
        return {"error": "unavailable"}


async def _section_holders(subject_id: str, chain: str) -> dict[str, Any]:
    """Fetch holder distribution section."""
    try:
        # Try domain token service first
        try:
            from app.domains.token.service import TokenService

            svc = TokenService()
            holders = await svc.get_holders(subject_id, chain, limit=20)
            return {
                "holder_count": len(holders),
                "top_holders": [
                    {
                        "address": h.address,
                        "balance": str(h.balance) if h.balance else None,
                        "percentage": h.percentage,
                    }
                    for h in holders[:10]
                ],
            }
        except ImportError:
            logger.debug("TokenService not available for holders, trying chain_feeder")
            # Fallback: try chain_feeder
            try:
                from app.chain_feeder import get_holders_for_token

                feed_holders = await get_holders_for_token(subject_id, limit=20)
                return {
                    "holder_count": len(feed_holders),
                    "top_holders": [{"address": h} for h in feed_holders[:10]],
                }
            except ImportError:
                logger.debug("ChainFeeder not available for holders")
                return {"error": "unavailable", "note": "No holder data available"}
            except Exception as e:
                logger.debug(f"ChainFeeder holders failed: {e}")
                return {"error": "unavailable", "note": "No holder data available"}
    except Exception as exc:
        logger.debug("section_holders_fail: %s", exc)
        return {"error": "unavailable"}


async def _section_transactions(subject_id: str, chain: str) -> dict[str, Any]:
    """Fetch recent transaction history section."""
    try:
        # Try unified provider for Solana
        if chain == "solana":
            try:
                from app.unified_provider import get_unified_provider

                provider = get_unified_provider()
                txs = await provider.get_wallet_transactions(subject_id, limit=10)
                return {
                    "transaction_count": len(txs),
                    "recent": [
                        {
                            "signature": t.get("signature", ""),
                            "timestamp": str(t.get("timestamp", "")),
                            "type": t.get("type", "unknown"),
                        }
                        for t in txs[:10]
                    ],
                }
            except (ImportError, Exception) as e:
                logger.debug(f"tx_unified_provider_failed: {e}")

        # Try Supabase service
        try:
            from app.services.supabase_service import get_transactions

            txs = await get_transactions(subject_id, chain, limit=10)
            return {
                "transaction_count": len(txs),
                "recent": [
                    {
                        "hash": t.get("transaction_hash", t.get("hash", "")),
                        "timestamp": str(t.get("block_timestamp", t.get("timestamp", ""))),
                        "type": "transfer",
                    }
                    for t in txs[:10]
                ],
            }
        except ImportError:
            logger.debug("Supabase service not available")
        except Exception as e:
            logger.debug(f"Supabase transactions failed: {e}")

        return {"error": "unavailable", "note": "Transaction history unavailable"}
    except Exception as exc:
        logger.debug("section_transactions_fail: %s", exc)
        return {"error": "unavailable"}


async def _section_news(subject_id: str) -> dict[str, Any]:
    """Fetch news/sentiment section."""
    try:
        # Try private intel MCP via direct HTTP or fall back
        from app.domains.x402.tools.news_tools import get_news_sentiment

        result = await get_news_sentiment(subject_id)
        if result.get("articles", 0) > 0 or result.get("news"):
            return result

        # Fallback: try news_service
        try:
            from app.domains.news.news_service import fetch_all_news

            news_result = await fetch_all_news(limit=10)
            news_items = news_result.get("articles", [])
            return {
                "articles": len(news_items),
                "sentiment": "neutral",
                "news": news_items[:5],
            }
        except ImportError:
            logger.debug("NewsService not available")
        except Exception as e:
            logger.debug(f"NewsService fallback failed: {e}")

        # Return minimal response instead of error
        return {
            "articles": 0,
            "sentiment": "neutral",
            "news": [],
            "note": "News service - no articles found for this subject",
        }
    except Exception as exc:
        logger.debug("section_news_fail: %s", exc)
        return {"error": "unavailable"}


async def _section_technical(subject_id: str, chain: str) -> dict[str, Any]:
    """Fetch technical / contract analysis section."""
    try:
        # Try contract deepscan
        try:
            from app.contract_deepscan import deep_scan_contract

            result = deep_scan_contract(subject_id, chain)
            if result:
                return {"contract_analysis": result}
        except ImportError:
            logger.debug("Contract deepscan not available")
        except Exception as e:
            logger.debug(f"Contract deepscan failed: {e}")

        # Try GoPlus security
        try:
            from app.caching_shield.service_mcp import ServiceMCP

            mcp = ServiceMCP()
            security = await mcp.gmgn.token_security(chain, subject_id)
            if security:
                return {"contract_analysis": security}
        except ImportError:
            logger.debug("ServiceMCP not available")
        except Exception as e:
            logger.debug(f"GoPlus security failed: {e}")

        return {"error": "unavailable", "note": "Technical analysis unavailable"}
    except Exception as exc:
        logger.debug("section_technical_fail: %s", exc)
        return {"error": "unavailable"}


# ── Helpers ─────────────────────────────────────────────────────────

_SECTION_HANDLERS: dict[str, Any] = {
    "risk": _section_risk,
    "holders": _section_holders,
    "transactions": _section_transactions,
    "news": _section_news,
    "technical": _section_technical,
}


def _detect_chain(subject_id: str) -> str:
    """Heuristic chain detection from address format."""
    sid = subject_id.lower().strip()
    if sid.startswith("0x") and len(sid) == 42:
        return "ethereum"
    if len(sid) >= 32 and len(sid) <= 44 and not sid.startswith("0x"):
        return "solana"
    return "ethereum"


def _format_markdown(result: dict[str, Any]) -> str:
    """Format report as Markdown string."""
    lines = [
        "# Custom Report",
        "",
        f"**Subject:** {result.get('subject_id', 'unknown')}",
        f"**Type:** {result.get('subject_type', 'unknown')}",
        f"**Generated:** {result.get('generated_at', 'now')}",
        "",
    ]
    for section_name, section_data in result.get("sections", {}).items():
        lines.append(f"## {section_name.title()}")
        lines.append("")
        if isinstance(section_data, dict):
            if "error" in section_data:
                lines.append(f"*{section_data['error']}*")
            else:
                for key, val in section_data.items():
                    if key != "note":
                        lines.append(f"- **{key}:** {val}")
        else:
            lines.append(str(section_data))
        lines.append("")
    return "\n".join(lines)


# ── Endpoint ────────────────────────────────────────────────────────


@router.post("/custom")
async def generate_report(request: ReportRequest) -> dict[str, Any]:
    """Generate a custom report with user-selected sections.

    Each section is fetched independently. If a section's data source
    is unavailable, it returns {"error": "unavailable"} rather than
    failing the entire report.

    Supports "json" (default) and "markdown" output formats.
    """
    chain = _detect_chain(request.subject_id)

    # ── Fan-out: fetch all requested sections in parallel ──────────
    section_futures: dict[str, asyncio.Task[Any]] = {}
    for section in request.sections:
        handler = _SECTION_HANDLERS.get(section)
        if handler is None:
            section_futures[section] = asyncio.create_task(_null_section(section, request.subject_id))
            continue

        if section == "risk":
            section_futures[section] = asyncio.create_task(handler(request.subject_type, request.subject_id, chain))
        elif section in ("holders", "transactions", "technical"):
            section_futures[section] = asyncio.create_task(handler(request.subject_id, chain))
        elif section == "news":
            section_futures[section] = asyncio.create_task(handler(request.subject_id))

    sections: dict[str, Any] = {}
    for section_name, fut in section_futures.items():
        try:
            sections[section_name] = await fut
        except Exception as exc:
            logger.warning("section_%s_failed: %s", section_name, exc)
            sections[section_name] = {"error": "unavailable"}

    # ── Compose response ───────────────────────────────────────────
    import datetime

    from app.catalog.models import utcnow

    result: dict[str, Any] = {
        "subject_type": request.subject_type,
        "subject_id": request.subject_id,
        "chain": chain,
        "generated_at": utcnow().isoformat()
        if hasattr(utcnow, "isoformat")
        else str(datetime.datetime.now(datetime.UTC)),
        "sections": sections,
    }

    if request.format == "markdown":
        result["markdown"] = _format_markdown(result)

    return result


async def _null_section(section: str, subject_id: str) -> dict[str, Any]:
    """Return empty section for unknown section types."""
    logger.warning("unknown_section=%s subject=%s", section, subject_id)
    return {"error": "unavailable", "note": f"Unknown section: {section}"}
