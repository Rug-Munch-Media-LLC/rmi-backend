"""Pryscraper Bridge Router — proxy endpoints for the Pry web intelligence API.

Proxies the Pry container's capabilities through the rmi-backend API
layer with consistent auth, rate limiting, error handling, and response
formatting.

Endpoints:
  POST /api/v1/scrape          — Scrape a single URL
  POST /api/v1/extract         — Extract structured data from a URL
  GET  /api/v1/news/search     — Search news articles via scraped sources
  GET  /api/v1/pryscraper/health — Health check passthrough
  GET  /api/v1/pryscraper/stats  — Client circuit-breaker stats
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.core.logging import get_logger
from app.services.pryscraper_client import PryscraperClient

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["pryscraper"])

# Singleton client (lazily initialised)
_client: PryscraperClient | None = None


def get_client() -> PryscraperClient:
    """Return the shared PryscraperClient singleton."""
    global _client
    if _client is None:
        _client = PryscraperClient()
    return _client


# ── Request / Response models ──────────────────────────────────────────────


class ScrapeRequest(BaseModel):
    """Request body for POST /api/v1/scrape."""

    url: str = Field(..., description="The URL to scrape")
    formats: list[str] | None = Field(None, description="Desired output formats")
    only_main_content: bool = Field(True, description="Extract only the main content")
    timeout: int = Field(30, ge=5, le=120, description="Scrape timeout in seconds")
    bypass_cloudflare: bool = Field(True, description="Auto-bypass Cloudflare")
    js_render: bool = Field(False, description="Render JavaScript before scrape")


class ExtractRequest(BaseModel):
    """Request body for POST /api/v1/extract."""

    url: str = Field(..., description="The URL to extract from")
    json_schema: dict[str, str] | None = Field(None, description="Structured extraction schema")
    timeout: int = Field(30, ge=5, le=120, description="Extraction timeout in seconds")


class NewsSearchRequest(BaseModel):
    """Request body for GET /api/v1/news/search."""

    query: str = Field(..., description="News search query")
    limit: int = Field(10, ge=1, le=50, description="Maximum results to return")


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/scrape", summary="Scrape a single URL via Pry")
async def proxy_scrape(
    req: ScrapeRequest,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Proxy to Pry's ``/v1/scrape`` endpoint.

    Requires authentication.  Returns scraped content in markdown format.
    """
    client = get_client()
    result = await client.scrape(
        url=req.url,
        formats=req.formats,
        only_main_content=req.only_main_content,
        timeout=req.timeout,
        bypass_cloudflare=req.bypass_cloudflare,
        js_render=req.js_render,
    )

    if not result.success:
        raise HTTPException(
            status_code=502,
            detail={
                "message": result.error or "Scrape failed",
                "source": result.source,
            },
        )

    return {
        "success": True,
        "data": result.data,
        "source": result.source,
    }


@router.post("/extract", summary="Extract structured data from a URL via Pry")
async def proxy_extract(
    req: ExtractRequest,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Proxy to Pry's ``/v1/extract`` endpoint.

    Requires authentication.  Returns structured data extracted from the URL.
    """
    client = get_client()
    result = await client.extract(
        url=req.url,
        json_schema=req.json_schema,
        timeout=req.timeout,
    )

    if not result.success:
        raise HTTPException(
            status_code=502,
            detail={
                "message": result.error or "Extraction failed",
                "source": result.source,
            },
        )

    return {
        "success": True,
        "data": result.data,
        "source": result.source,
    }


@router.get("/news/search", summary="Search news via Pry scraper")
async def proxy_news_search(
    query: str = Query(..., description="News search query"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Search for news articles using Pry's web intelligence.

    Requires authentication.  Returns scraped news article data.
    """
    client = get_client()
    result = await client.search_news(query=query, limit=limit)

    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail={
                "message": result.get("error", "News search failed"),
                "source": "pryscraper",
            },
        )

    return {
        "success": True,
        "data": result.get("articles", []),
        "source": result.get("source", "pryscraper"),
    }


@router.get("/pryscraper/health", summary="Pry health check passthrough")
async def proxy_health() -> dict[str, Any]:
    """Check Pry container health.  Does **not** require auth."""
    client = get_client()
    try:
        health = await client.check_health()
        return {
            "success": True,
            "data": health,
            "source": "pryscraper",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": f"Pryscraper unreachable: {exc}",
                "source": "pryscraper",
            },
        )


@router.get("/pryscraper/stats", summary="Pryscraper client stats")
async def proxy_stats(
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Return circuit-breaker and client statistics."""
    client = get_client()
    stats = await client.stats()
    return {
        "success": True,
        "data": stats,
    }
