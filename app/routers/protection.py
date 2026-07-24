#!/usr/bin/env python3
"""

Protection Suite API Endpoints
Provides safety checking for URLs, wallets, and tokens.

Dependency injection: all external clients are injected via FastAPI's
``Depends()`` so they can be mocked in tests.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.dependencies import get_http_client, get_rate_limiter
from app.core.errors import AppError, RateLimitError, ValidationError
from app.core.logging import get_logger
from app.core.rate_limiter import UnifiedRateLimiter

logger = get_logger("protection")

# ── Rate Limiter ─────────────────────────────────────────────────────────────
# Uses UnifiedRateLimiter injected via Depends.

async def _check_rate_limit(
    request: Request,
    limit_key: str,
    req_per_min: int,
    limiter: UnifiedRateLimiter,
) -> None:
    """Check rate limit. Raises 429 if exceeded."""
    client_id = get_client_id(request)
    rate = req_per_min / 60.0
    burst = max(1, req_per_min // 12)
    allowed, headers = await limiter.acquire(
        f"protection:{limit_key}:{client_id}", rate=rate, burst=burst
    )
    if not allowed:
        retry_after = headers.get("Retry-After", "60")
        raise RateLimitError(f"Rate limit exceeded. Retry after {retry_after} seconds.")

def get_client_id(request: Request) -> str:
    """Extract client identifier for rate limiting."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# ── Router Definition ─────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/v1/protect", tags=["protection"])

# Pydantic models for request/response
class URLCheckRequest(BaseModel):
    url: str

class WalletCheckRequest(BaseModel):
    address: str
    chain: str = "ethereum"

class TokenCheckRequest(BaseModel):
    address: str
    chain: str

class ProtectionResponse(BaseModel):
    safe: bool
    risk: str = "low"  # low, medium, high, critical
    reason: str = ""
    source: str = ""  # rag, scanner, blocklist, etc.
    risk_score: int | None = None  # 0-100 for wallet/token
    flags: list[str] = []  # for wallet: ["drainer", "scammer", ...]
    error: str | None = None

async def check_rag_for_url(url: str) -> dict[str, Any] | None:
    """Check RAG for known scam URL."""
    try:
        from app.rag_service import three_pillar_search

        results = await asyncio.wait_for(
            three_pillar_search(
                query=url,
                collections=["known_scams", "scam_patterns"],
                limit=1,
                min_similarity=0.7,
                use_mmr=False,
                use_kg=False,
                use_parent_child=False,
                use_reranker=False,
            ),
            timeout=0.2,
        )
        if results and results.get("results"):
            for res in results["results"]:
                if res.get("similarity", 0) >= 0.7:
                    return {
                        "safe": False,
                        "risk": "critical",
                        "reason": f"Known scam site per RAG (similarity: {res['similarity']:.2f})",
                        "source": "rag",
                    }
    except TimeoutError:
        logger.debug(f"RAG check timed out for {url}")
    except Exception as e:
        logger.debug(f"RAG check failed for {url}: {e}")
    return None

async def check_blocklist_for_url(url: str) -> dict[str, Any] | None:
    """Check URL against blocklist.json domains."""
    try:
        blocklist_path = "/app/blocklist.json"
        if not os.path.exists(blocklist_path):
            return None
        with open(blocklist_path) as f:
            blocklist = json.load(f)
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain in blocklist:
            return {
                "safe": False,
                "risk": "critical",
                "reason": "Domain on blocklist",
                "source": "blocklist",
            }
    except Exception as e:
        logger.debug(f"Blocklist check failed for {url}: {e}")
    return None

async def scan_url_content(url: str, client: httpx.AsyncClient) -> dict[str, Any] | None:
    """Scan URL content for malicious patterns (simple check)."""
    try:
        resp = await asyncio.wait_for(
            client.get(
                str(url),
                follow_redirects=True,
                headers={"User-Agent": "RMI-Protection-Bot"},
                timeout=1.5,
            ),
            timeout=2.0,
        )
        if resp.status_code != 200:
            return None
        text = resp.text.lower()
        phishing_indicators = [
            "verify your account",
            "suspend",
            "urgent action required",
            "private key",
            "seed phrase",
            "recovery phrase",
            "connect wallet",
            "claim your reward",
            "airdrop",
            "free tokens",
        ]
        for indicator in phishing_indicators:
            if indicator in text:
                return {
                    "safe": False,
                    "risk": "high",
                    "reason": f"Phishing indicator: {indicator}",
                    "source": "scanner",
                }
    except TimeoutError:
        logger.debug(f"URL content scan timed out for {url}")
    except Exception as e:
        logger.debug(f"URL content scan failed for {url}: {e}")
    return None

async def feed_url_to_rag(url: str, is_safe: bool) -> None:
    """Feed URL scan result back to RAG for learning (best effort)."""
    try:
        from app.rag_service import ingest_document

        doc = {
            "content": f"URL: {url}\nSafety: {'safe' if is_safe else 'unsafe'}\nScanned by RMI Protection Suite",
            "metadata": {"source": "protection_suite", "url": url, "safe": is_safe},
        }
        await ingest_document(doc)
    except Exception as e:
        logger.debug(f"Failed to feed URL to RAG: {e}")

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/check-url", response_model=ProtectionResponse)
async def check_url(
    request: URLCheckRequest,
    http_request: Request,
    client: httpx.AsyncClient = Depends(get_http_client),
    limiter: UnifiedRateLimiter = Depends(get_rate_limiter),
) -> Any:
    """Check a URL for phishing/scam content."""
    try:
        await _check_rate_limit(http_request, "check-url", 60, limiter)

        url = request.url.strip()
        if not url:
            raise ValidationError("URL is required")

        blocklist_task = asyncio.create_task(check_blocklist_for_url(url))
        rag_task = asyncio.create_task(check_rag_for_url(url))
        scan_task = asyncio.create_task(scan_url_content(url, client))

        blocklist_result, rag_result, scan_result = await asyncio.gather(
            blocklist_task, rag_task, scan_task, return_exceptions=False
        )

        if blocklist_result and not isinstance(blocklist_result, Exception):
            return ProtectionResponse(**blocklist_result)

        if rag_result and not isinstance(rag_result, Exception):
            return ProtectionResponse(**rag_result)

        if scan_result and not isinstance(scan_result, Exception):
            await feed_url_to_rag(url, is_safe=scan_result.get("safe", True))
            return ProtectionResponse(**scan_result)

        await feed_url_to_rag(url, is_safe=True)
        return ProtectionResponse(
            safe=True, risk="low", reason="No threats detected", source="protection_suite"
        )
    except (HTTPException, AppError):
        raise
    except Exception as e:
        logger.warning(f"URL check failed for {request.url}: {e}")
        return ProtectionResponse(
            safe=True,
            risk="low",
            reason="URL scan unavailable",
            source="error",
            error="service_unavailable",
        )

@router.post("/check-wallet", response_model=ProtectionResponse)
async def check_wallet(
    request: WalletCheckRequest,
    http_request: Request,
    limiter: UnifiedRateLimiter = Depends(get_rate_limiter),
) -> Any:
    """Check a wallet address for risk."""
    try:
        await _check_rate_limit(http_request, "check-wallet", 30, limiter)

        address = request.address.strip()
        chain = request.chain.lower().strip()
        if not address:
            raise ValidationError("Address is required")

        valid_chains = {
            "ethereum",
            "solana",
            "base",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "avalanche",
            "fantom",
        }
        if chain not in valid_chains:
            chain = "ethereum"

        from app.unified_scanner import scan_wallet as wallet_scan

        try:
            result = await asyncio.wait_for(wallet_scan(address, chain), timeout=1.8)
        except TimeoutError:
            logger.warning(f"Wallet scan timed out for {address}")
            return ProtectionResponse(
                safe=True,
                risk="low",
                reason="Wallet scan timed out",
                source="error",
                error="service_unavailable",
            )

        risk_score = getattr(result, "total_risk_score", 50)
        if risk_score >= 80:
            risk_level = "critical"
        elif risk_score >= 60:
            risk_level = "high"
        elif risk_score >= 40:
            risk_level = "medium"
        else:
            risk_level = "low"
        flags = []
        if hasattr(result, "is_labeled_scammer") and result.is_labeled_scammer:
            flags.append("scammer")
        if hasattr(result, "is_labeled_sanctioned") and result.is_labeled_sanctioned:
            flags.append("sanctioned")
        if hasattr(result, "approval_risk_score") and result.approval_risk_score > 70:
            flags.append("approval_risk")
        return ProtectionResponse(
            safe=risk_score < 60,
            risk=risk_level,
            reason=f"Wallet risk score: {risk_score}",
            source="unified_scanner",
            risk_score=risk_score,
            flags=flags,
        )
    except (HTTPException, AppError):
        raise
    except Exception as e:
        logger.warning(f"Wallet scan failed for {request.address}: {e}")
        return ProtectionResponse(
            safe=True,
            risk="low",
            reason="Wallet scan unavailable",
            source="error",
            error="service_unavailable",
        )

@router.post("/check-token", response_model=ProtectionResponse)
async def check_token(
    request: TokenCheckRequest,
    http_request: Request,
    limiter: UnifiedRateLimiter = Depends(get_rate_limiter),
) -> Any:
    """Check a token address for safety."""
    try:
        await _check_rate_limit(http_request, "check-token", 30, limiter)

        address = request.address.strip()
        chain = request.chain.lower().strip()
        if not address:
            raise ValidationError("Address is required")
        if not chain:
            raise ValidationError("Chain is required")

        from app.token_scanner import scan_token

        try:
            scan_result = await asyncio.wait_for(
                scan_token(address, chain, tier="free", protection_mode=True), timeout=1.8
            )
        except TimeoutError:
            logger.warning(f"Token scan timed out for {address}")
            return ProtectionResponse(
                safe=True,
                risk="low",
                reason="Token scan timed out",
                source="error",
                error="service_unavailable",
            )
        safety_score = scan_result.safety_score
        if safety_score >= 80:
            risk_level = "low"
        elif safety_score >= 60:
            risk_level = "medium"
        elif safety_score >= 40:
            risk_level = "high"
        else:
            risk_level = "critical"
        flags = scan_result.risk_flags if hasattr(scan_result, "risk_flags") else []
        source = "token_scanner"
        if hasattr(scan_result, "modules_run") and scan_result.modules_run:
            source = (
                scan_result.modules_run[0].get("module", "token_scanner")
                if scan_result.modules_run
                else "token_scanner"
            )
        return ProtectionResponse(
            safe=safety_score >= 60,
            risk=risk_level,
            reason=f"Token safety score: {safety_score}",
            source=source,
            risk_score=safety_score,
            flags=flags,
        )
    except (HTTPException, AppError):
        raise
    except Exception as e:
        logger.warning(f"Token scan failed for {request.address}: {e}")
        return ProtectionResponse(
            safe=True,
            risk="low",
            reason="Token scan unavailable",
            source="error",
            error="service_unavailable",
        )

@router.get("/domains")
async def get_domains() -> dict | dict[str, list]:
    """Return the blocklist JSON for extension download."""
    logger.info("=== get_domains endpoint called ===")
    try:
        extension_blocklist_path = "/root/browser-extension/blocklist.json"
        backend_blocklist_path = "/app/blocklist.json"
        domains_set: set[str] = set()

        if os.path.exists(extension_blocklist_path):
            with open(extension_blocklist_path) as f:
                extension_domains = json.load(f)
                domains_set.update(extension_domains)
        else:
            logger.warning(f"Extension blocklist file does not exist: {extension_blocklist_path}")

        if os.path.exists(backend_blocklist_path):
            with open(backend_blocklist_path) as f:
                backend_domains = json.load(f)
                domains_set.update(backend_domains)

        try:
            from app.rag_service import three_pillar_search

            rag_results = await asyncio.wait_for(
                three_pillar_search(
                    query="",
                    collections=["known_scams"],
                    limit=100,
                    min_similarity=0.0,
                    use_mmr=False,
                    use_kg=False,
                    use_parent_child=False,
                    use_reranker=False,
                ),
                timeout=0.5,
            )
            if rag_results and rag_results.get("results"):
                for res in rag_results["results"]:
                    content = res.get("content", "")
                    for line in content.split("\n"):
                        if line.startswith("URL: "):
                            url = line[5:].strip()
                            if url:
                                try:
                                    parsed = urlparse(url)
                                    domain = parsed.netloc.lower()
                                    if domain.startswith("www."):
                                        domain = domain[4:]
                                    if domain:
                                        domains_set.add(domain)
                                except (ValueError, TypeError):
                                    pass
                            break
        except TimeoutError:
            logger.debug("RAG query for domains timed out")
        except Exception as e:
            logger.debug(f"Failed to extract domains from RAG: {e}")

        domains = sorted(domains_set)
        return {"domains": domains, "count": len(domains)}
    except Exception as e:
        logger.error(f"Failed to load blocklist: {e}", exc_info=True)
        return {"domains": [], "count": 0}

@router.get("/health")
async def protection_health() -> Any:
    """Health check for protection modules."""
    health_status = {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "modules": {
            "rag": "unknown",
            "blocklist": "unknown",
            "unified_scanner": "unknown",
            "token_scanner": "unknown",
        },
    }
    try:
        blocklist_path = "/app/blocklist.json"
        if os.path.exists(blocklist_path):
            with open(blocklist_path) as f:
                json.load(f)
            health_status["modules"]["blocklist"] = "ok"
        else:
            health_status["modules"]["blocklist"] = "missing_file"
    except Exception as e:
        health_status["modules"]["blocklist"] = f"error: {e}"

    try:
        from app.rag_service import three_pillar_search

        await three_pillar_search(
            query="test", collections=["known_scams"], limit=1, min_similarity=0.5
        )
        health_status["modules"]["rag"] = "ok"
    except Exception as e:
        health_status["modules"]["rag"] = f"error: {e}"

    try:
        health_status["modules"]["unified_scanner"] = "ok"
    except Exception as e:
        health_status["modules"]["unified_scanner"] = f"error: {e}"

    try:
        health_status["modules"]["token_scanner"] = "ok"
    except Exception as e:
        health_status["modules"]["token_scanner"] = f"error: {e}"

    critical_modules = ["rag", "blocklist", "unified_scanner", "token_scanner"]
    for mod in critical_modules:
        if health_status["modules"][mod] != "ok":
            health_status["status"] = "degraded"
            break

    return health_status
