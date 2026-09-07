"""Pryscraper Bridge Router — proxy endpoints for the Pry web intelligence API.

Proxies the Pry container's capabilities through the rmi-backend API
layer with consistent auth, rate limiting, error handling, and response
formatting.

Dependency injection: ``PryscraperClient`` is injected via ``Depends()``
so it can be mocked in tests.

Endpoints:
  POST /api/v1/scrape          — Scrape a single URL
  POST /api/v1/extract         — Extract structured data from a URL
  GET  /api/v1/news/search     — Search news articles via scraped sources
  GET  /api/v1/pryscraper/health — Health check passthrough
  GET  /api/v1/pryscraper/stats  — Client circuit-breaker stats
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator

from app.auth import require_auth
from app.core.errors import UpstreamError
from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency
from app.core.shared_utils import validate_public_url
from app.integrations.pryscraper_client import get_pryscraper_client

if TYPE_CHECKING:
    from app.integrations.pryscraper_client import PryscraperClient

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["pryscraper"])

# ── Request / Response models ──────────────────────────────────────────────


class ScrapeRequest(BaseModel):
    """Request body for POST /api/v1/scrape."""

    url: str = Field(..., description="The URL to scrape")
    formats: list[str] | None = Field(default=None, description="Desired output formats")
    only_main_content: bool = Field(default=True, description="Extract only the main content")
    timeout: int = Field(default=30, ge=5, le=120, description="Scrape timeout in seconds")
    bypass_cloudflare: bool = Field(default=True, description="Auto-bypass Cloudflare")
    js_render: bool = Field(default=False, description="Render JavaScript before scrape")

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return validate_public_url(v)


class ExtractRequest(BaseModel):
    """Request body for POST /api/v1/extract."""

    url: str = Field(..., description="The URL to extract from")
    json_schema: dict[str, str] | None = Field(default=None, description="Structured extraction schema")
    timeout: int = Field(default=30, ge=5, le=120, description="Extraction timeout in seconds")
    bypass_cloudflare: bool = Field(default=True, description="Auto-bypass Cloudflare")
    js_render: bool = Field(default=False, description="Render JavaScript before extraction")


class NewsSearchRequest(BaseModel):
    """Request body for POST /api/v1/news/search."""

    query: str = Field(..., description="Search query")
    max_results: int = Field(default=10, ge=1, le=50, description="Maximum results to return")
    days_back: int = Field(default=7, ge=1, le=30, description="Number of days to search back")


class ScrapeResponse(BaseModel):
    """Standardised scrape/extract response."""

    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    model: str | None = None
    error: str | None = None
    timing_ms: int | None = None
    job_id: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/scrape")
async def scrape_url(
    request: ScrapeRequest,
    client: PryscraperClient = Depends(get_pryscraper_client),
) -> ScrapeResponse:
    """Scrape a single URL via the Pry service.

    The Pry container fetches the URL, extracts markdown/text/html, and
    returns the result.  This endpoint proxies that result with consistent
    auth and error handling.
    """
    try:
        result = await client.scrape(
            url=request.url,
            formats=request.formats,
            only_main_content=request.only_main_content,
            timeout=request.timeout,
            bypass_cloudflare=request.bypass_cloudflare,
            js_render=request.js_render,
        )
        return ScrapeResponse(
            success=result.success,
            data=result.data if result.success else {},
            source=result.data.get("source") if result.success else None,
            error=result.error,
            timing_ms=None,
        )
    except Exception as exc:
        logger.error("pryscraper_scrape_failed", url=request.url[:60], exc_info=exc)
        raise UpstreamError(str(exc)[:500]) from exc


@router.post("/extract")
async def extract_url(
    request: ExtractRequest,
    client: PryscraperClient = Depends(get_pryscraper_client),
) -> ScrapeResponse:
    """Extract structured data from a URL via the Pry service."""
    try:
        result = await client.extract(
            url=request.url,
            json_schema=request.json_schema,
            timeout=request.timeout,
        )
        return ScrapeResponse(
            success=result.success,
            data=result.data if result.success else {},
            error=result.error,
            source="pryscraper",
            timing_ms=None,
        )
    except Exception as exc:
        logger.error("pryscraper_extract_failed", url=request.url[:60], exc_info=exc)
        raise UpstreamError(str(exc)[:500]) from exc


@router.get("/news/search")
async def search_news(
    q: str = Query(..., description="Search query"),
    max_results: int = Query(10, ge=1, le=50),
    days_back: int = Query(7, ge=1, le=30),
    client: PryscraperClient = Depends(get_pryscraper_client),
) -> list[dict[str, Any]]:
    """Search news articles via the Pry scraper's search capability."""
    try:
        results = await client.search_news(query=q, limit=max_results)
        articles = results.get("articles", [])
        return articles if isinstance(articles, list) else []
    except Exception as exc:
        logger.error("pryscraper_news_search_failed", query=q[:60], exc_info=exc)
        raise UpstreamError(str(exc)[:500]) from exc


@router.get("/pryscraper/health")
async def pryscraper_health(
    client: PryscraperClient = Depends(get_pryscraper_client),
) -> dict[str, Any]:
    """Health check passthrough to the Pry service."""
    try:
        health = await client.check_health()
        return {"status": "ok", "pryscraper": health}
    except Exception as exc:
        logger.error("pryscraper_health_failed", exc_info=exc)
        return {"status": "degraded", "error": str(exc)[:200]}


@router.get("/pryscraper/stats")
async def pryscraper_stats(
    client: PryscraperClient = Depends(get_pryscraper_client),
) -> dict[str, Any]:
    """Client circuit-breaker statistics."""
    try:
        return await client.stats()
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200]}


# ── RAG ingestion integration ──────────────────────────────────────────────


class IngestNewsRequest(BaseModel):
    """Request body for POST /api/v1/rag/ingest-news."""

    title: str = Field(..., min_length=1, max_length=1024, description="Article title")
    body: str = Field(..., min_length=1, description="Article body / content")
    source_url: str = Field(..., description="Original article URL")
    source_name: str = Field(default="", description="Source publication name")
    published_at: str = Field(default="", description="ISO-8601 publish timestamp")
    categories: list[str] = Field(default_factory=list, description="Topic categories")
    sentiment: float = Field(default=0.0, ge=-1.0, le=1.0, description="Sentiment score -1..1")


@router.post("/rag/ingest-news")
async def ingest_news_webhook(
    request: IngestNewsRequest,
    _auth: dict[str, Any] = Depends(require_auth),
    _rl: None = Depends(get_rate_limit_dependency("rag_ingest", rate=1.0, burst=5)),
) -> dict[str, Any]:
    """Webhook: ingest a scraped news article into the RAG ``news`` collection.

    Called automatically after Pryscraper scrapes a news article.
    The article is chunked, embedded, and stored for semantic search.

    Auth: JWT or API key required.
    Rate limit: 1 req/s burst 5 (token-bucket).
    """
    try:
        from app.integrations.rugbrain_client import rugbrain_ingest

        content = f"Title: {request.title}\nBody: {request.body}"
        metadata = {
            "source_url": request.source_url,
            "source_name": request.source_name,
            "published_at": str(request.published_at) if request.published_at else "",
            "categories": request.categories or [],
            "sentiment": request.sentiment or "",
        }
        ok = await rugbrain_ingest(content=content, collection="news_v2", metadata=metadata)
        report = {"ingested": ok, "title": request.title}
        logger.info(
            "news_ingested",
            title=request.title[:60],
            status=report.status,
            chunks=report.chunks_total,
        )
        return {
            "success": True,
            "status": report.status,
            "doc_id": f"news:{request.title[:12]}",
            "chunks": report.chunks_total,
            "accepted": report.accepted,
            "rejected": report.rejected,
        }
    except Exception as exc:
        logger.error("news_ingest_failed", title=request.title[:60], exc_info=exc)
        return {
            "success": False,
            "status": "failed",
            "error": str(exc)[:500],
        }
