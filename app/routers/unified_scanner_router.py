"""Unified Scanner Router v3 - matches WalletScanner v3 fields.

Dependency injection: ``UnifiedTokenScanner`` and ``UnifiedWalletScanner``
are injected via ``Depends()`` so they can be mocked in tests.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger
from app.features import get_token_scanner, get_wallet_scanner

if TYPE_CHECKING:
    from app.domains.scanner.unified import UnifiedTokenScanner
    from app.domains.wallet.unified_wallet_scanner import UnifiedWalletScanner

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v2/scanner", tags=["scanner-v2"])


class TokenScanRequest(BaseModel):
    address: str
    chain: str = "solana"


class WalletScanRequest(BaseModel):
    address: str
    chain: str = ""  # empty = auto-detect from address format (0x → ethereum, base58 → solana)


def _get_tier(request: Request) -> str:
    # SECURITY (TOP-100 #8, 2026-08-23): the client-supplied
    # ``X-Payment-Verified`` header is no longer honored — anyone could set it
    # and unlock pro-tier scans / bypass the wallet-data paywall. Paid tiers
    # must be established server-side (x402 payment settlement or an internal
    # key), never from headers an anonymous client controls.
    import hmac as _hmac

    supplied_key = request.headers.get("X-RMI-Key", "")
    if supplied_key and _hmac.compare_digest(supplied_key, INTERNAL_API_KEY):
        return "admin"
    return "free"


def _get_admin_key(request: Request) -> Any | None:
    return request.headers.get("X-Admin-Key") or request.headers.get("X-RMI-Key")


def _detect_chain(address: str) -> str:
    """Detect the blockchain from the address format.

    0x prefix = EVM address → ethereum. Anything else (base58) → solana.
    """
    addr = (address or "").strip()
    if addr.startswith(("0x", "0X")):
        return "ethereum"
    return "solana"


def _serialize_rag_context(rag_ctx: Any) -> dict[str, Any] | None:
    """Best-effort serialize RAGContext for the API response.

    Mirrors the compact shape the legacy scanner stores (see
    ``app/domains/scanner/service/scan.py``). Never raises — RAG
    enrichment is best-effort, so a broken RAG payload must not fail
    the scan response.
    """
    if rag_ctx is None:
        return None
    try:
        return {
            "similar_scams": [
                {
                    "content": h.content[:200],
                    "score": round(h.final_score or h.score, 4),
                    "collection": h.collection,
                    "doc_id": h.doc_id,
                }
                for h in rag_ctx.similar_scams[:5]
            ],
            "wallet_context": [
                {
                    "content": h.content[:200],
                    "score": round(h.final_score or h.score, 4),
                    "collection": h.collection,
                }
                for h in rag_ctx.wallet_context[:3]
            ],
            "market_signals": len(rag_ctx.market_context),
            "fusion_summary": rag_ctx.fusion_summary,
            "collections_searched": rag_ctx.collections_searched,
            "took_ms": rag_ctx.took_ms,
            "query_id": rag_ctx.query_id,
        }
    except Exception:
        return None


async def _attach_ai_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach the AI narrative verdict (fail-open) — async context required."""
    try:
        from app.services.scan_verdict_ai import synthesize_scan_verdict

        verdict = await synthesize_scan_verdict(payload)
        if verdict:
            payload["ai_verdict"] = verdict
    except Exception:
        payload["ai_verdict"] = None
    return payload


@router.post(
    "/token/scan",
    summary="Scan Token",
    description="Comprehensive token scan across 50+ security modules. Returns safety score, risk flags, green flags, and detailed module results.",
)
async def scan_token(
    request: Request,
    body: TokenScanRequest,
    scanner: UnifiedTokenScanner = Depends(get_token_scanner),
) -> dict[str, Any]:
    tier = _get_tier(request)
    return await _run_scan(scanner, body, tier, request)


@router.post(
    "/token/deepscan",
    summary="Deep Scan Token",
    description="Full deep scan — runs ALL modules including premium/SENTINEL (bundle detect, cluster map, dev finder, wash trade, MEV, sniper, insider, bot farm, holder analysis, LP verification, contract authority, proxy detection). Not gated — free to use.",
)
async def deepscan_token(
    request: Request,
    body: TokenScanRequest,
    scanner: UnifiedTokenScanner = Depends(get_token_scanner),
) -> dict[str, Any]:
    """Deep scan: forces elite tier to unlock all premium + SENTINEL modules.

    The regular /token/scan runs CORE + DEEP + INTEL phases (~20 modules).
    Deepscan turns on PREMIUM chains (bundle, cluster, dev-finder, sniper,
    bot-farm, copy-trade, insider, wash-trade, MEV, fresh-wallets) and
    SENTINEL free modules (holder analysis, LP verification, dev reputation,
    metadata fingerprint, contract authority, proxy detection).
    """
    admin_key = _get_admin_key(request)
    try:
        result = await scanner.scan(address=body.address, chain=body.chain, tier="elite", admin_key=admin_key)
        return await _attach_ai_verdict(_format_scan_response(result))
    except Exception as e:
        raise HTTPException(500, f"Deep scan failed: {str(e)[:200]}") from e


async def _run_scan(
    scanner: UnifiedTokenScanner,
    body: TokenScanRequest,
    tier: str,
    request: Request,
) -> dict[str, Any]:
    """Run a token scan and format the response."""
    admin_key = _get_admin_key(request)
    try:
        result = await scanner.scan(address=body.address, chain=body.chain, tier=tier, admin_key=admin_key)
        return await _attach_ai_verdict(_format_scan_response(result))
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {str(e)[:200]}") from e


def _top_level_deployer(result: Any) -> dict[str, Any] | None:
    """Best-effort top-level deployer address for thin clients (bot + site).

    2026-08-25 quality push — "we always know the deployer": every scan
    response carries ONE canonical ``deployer_address`` field so consumers
    never dig through sentinel blobs / evidence nesting. Checked in order:
    dev_reputation evidence → SENTINEL free dev_reputation → SENTINEL
    metadata_fingerprint creator → deep_profile.deployer_profile.
    """
    candidates: list[tuple[str, Any]] = []
    dev = getattr(result, "dev_reputation", None)
    if isinstance(dev, dict):
        candidates.append(("dev_reputation", dev.get("deployer_address") or dev.get("address")))
    sent = getattr(result, "sentinel", None)
    if isinstance(sent, dict):
        free = sent.get("free") if isinstance(sent.get("free"), dict) else {}
        dr = free.get("dev_reputation")
        if isinstance(dr, dict):
            candidates.append(("dev_reputation", dr.get("deployer_address") or dr.get("address")))
        mf = free.get("metadata_fingerprint")
        if isinstance(mf, dict):
            candidates.append(("metadata_fingerprint", mf.get("creator")))
    dp = getattr(result, "deep_profile", None)
    dpp = getattr(dp, "deployer_profile", None) if dp is not None else None
    if dpp is not None:
        addr = getattr(dpp, "address", None) if not isinstance(dpp, dict) else dpp.get("address")
        candidates.append(("deployer_profile", addr))
    _empty = ("", "unknown", "null", "none", "0x", "0x0")
    for source, addr in candidates:
        if isinstance(addr, str) and len(addr) >= 32 and addr.strip().lower() not in _empty:
            return {"address": addr.strip(), "source": source}
    return None


def _format_scan_response(result: Any) -> dict[str, Any]:
    """Format a ScanResult into the API response dict."""
    # Track which sources were own data vs external API fallback
    own_data_modules = sum(1 for s in result.enrichment_sources if "own" in s.split(":")[-1].lower())
    fallback_modules = sum(
        1
        for s in result.enrichment_sources
        if "own" not in s.split(":")[-1].lower() and s.split(":")[-1] not in ("", "unknown")
    )
    total_with_source = len(result.enrichment_sources)
    fallback_used = total_with_source > 0 and fallback_modules >= own_data_modules
    deployer_top = _top_level_deployer(result)

    payload: dict[str, Any] = {
        "status": "ok",
        "token": result.token_address,
        "chain": result.chain,
        "name": result.name,
        "symbol": result.symbol,
        "price_usd": result.price_usd,
        "market_cap_usd": result.market_cap_usd,
        "liquidity_usd": result.liquidity_usd,
        "volume_24h_usd": result.volume_24h_usd,
        "age_hours": result.age_hours,
        "safety_score": result.safety_score,
        "confidence": result.confidence,
        "risk_level": result.risk_level,
        "risk_flags": result.risk_flags,
        "green_flags": result.green_flags,
        "breakdown": result.breakdown,
        "evidence": result.evidence,
        "modules_run": result.modules_run,
        "scan_duration_ms": result.scan_duration_ms,
        "tier": result.tier,
        "scanned_at": result.scanned_at,
        "background_status": result.background_status,
        "cached": result.cached,
        "stability": result.stability,
        "data_source": "databus",
        "freshness": result.scanned_at,
        "fallback_used": fallback_used,
        # Canonical top-level deployer (2026-08-25 quality push) — one field
        # every surface can render without nested digging.
        "deployer_address": (deployer_top or {}).get("address"),
        "deployer_source": (deployer_top or {}).get("source"),
        "data_sources": {
            "own_data_modules": own_data_modules,
            "external_fallback_modules": fallback_modules,
            "total_modules": len(result.modules_run),
        },
        "rag_context": _serialize_rag_context(result.rag_context),
        "news_sentiment": result.news_sentiment,
        "news_card": result.news_card,
        "sentinel": result.sentinel,
        "wallet_metrics": result.wallet_metrics,
        "bundle_analysis": result.bundle_analysis,
        "first_buyers": result.first_buyers,
        "deployer_profile": (
            asdict(result.deep_profile.deployer_profile)
            if result.deep_profile
            and is_dataclass(result.deep_profile.deployer_profile)
            and not isinstance(result.deep_profile.deployer_profile, type)
            else (
                result.deep_profile.deployer_profile
                if result.deep_profile and result.deep_profile.deployer_profile
                else None
            )
        ),
    }
    # ai_verdict: wired at the ASYNC route layer (see scan_verdict_ai.py) —
    # this formatter is sync and cannot await.
    return payload


@router.get(
    "/token/scan/{chain}/{address}/status",
    summary="Token Scan Status",
    description=(
        "Get the status of an in-flight token scan (or a completed cached result). "
        "The scan endpoint returns within ~25s while deep/intel/SENTINEL/news "
        "phases continue in the background; poll this endpoint for progress and "
        "the final full result."
    ),
)
async def get_token_scan_status(chain: str, address: str) -> dict[str, Any]:
    """Get status/progress of an in-flight scan (or a completed cached result)."""
    from app.domains.scanner.unified.scanner import scan_status

    status = await scan_status(chain, address)
    if status is None:
        raise HTTPException(404, f"No scan status found for {chain}:{address}")
    return status


@router.post(
    "/wallet/scan",
    summary="Scan Wallet",
    description="Comprehensive wallet scan with entity clustering, risk scoring, behavioral analysis, and RAG-based threat intelligence matching.",
)
async def scan_wallet(
    request: Request,
    body: WalletScanRequest,
    scanner: UnifiedWalletScanner = Depends(get_wallet_scanner),
) -> dict[str, Any]:
    tier = _get_tier(request)
    admin_key = _get_admin_key(request)

    # P0-5: wallet data is tier-gated (PUBLIC_WEB denied in the access matrix).
    # Never return a false-success 200 with zero-filled wallet fields — tell the
    # caller to sign up instead.
    if tier == "free":
        return {
            "data_gated": "requires_auth",
            "message": "Sign up for free beta to unlock wallet data",
        }

    # Auto-detect chain from address format when not explicitly provided
    # (default was "solana", which misrouted 0x EVM addresses).
    chain = body.chain or _detect_chain(body.address)

    try:
        result = await scanner.scan(address=body.address, chain=chain, tier=tier, admin_key=admin_key)
        # Defense in depth: if a databus fetch still came back denied (e.g. bad
        # admin key), surface the gate instead of the zero-filled scan result.
        if result.data_gated:
            return {
                "data_gated": result.data_gated,
                "message": "Sign up for free beta to unlock wallet data",
            }
        return {
            "status": "ok",
            "address": result.address,
            "chain": result.chain,
            "labels": result.labels,
            "entity_name": result.entity_name,
            "entity_type": result.entity_type,
            "persona": result.persona,
            "persona_confidence": result.persona_confidence,
            "total_value_usd": result.total_value_usd,
            "token_count": result.token_count,
            "scam_tokens_held": result.scam_tokens_held,
            "scam_exposure_pct": result.scam_exposure_pct,
            "risk_score": result.risk_score,
            "risk_flags": result.risk_flags,
            "rag_matches": len(result.rag_matches),
            "behavior_patterns": result.behavior_patterns,
            "smart_money_overlap": result.smart_money_overlap,
            "risk_forecast": result.risk_forecast,
            "modules_run": result.modules_run,
            "scan_duration_ms": result.scan_duration_ms,
            "tier": result.tier,
            "scanned_at": result.scanned_at,
            "data_source": "databus",
            "freshness": result.scanned_at,
            "fallback_used": result.risk_score is None,
        }
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {str(e)[:200]}") from e
