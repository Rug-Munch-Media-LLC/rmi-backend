"""
Scoring API Router — Token Rating & Deployer Detection
========================================================
REST API for the RugMunch multi-factor token rating engine
and real deployer detection system.

Endpoints:
    POST /api/v1/scoring/rate              — Rate any token (full breakdown)
    GET  /api/v1/scoring/deployer/{chain}/{address} — Deployer profile
    GET  /api/v1/scoring/wallet/{chain}/{address}   — Wallet labels

Mounted by: app.mount.py (add "app.routers.scoring_router")
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/scoring", tags=["scoring"])


# ── Request / Response Models ──


class RateTokenRequest(BaseModel):
    """Request to rate a token."""

    address: str = Field(
        ...,
        min_length=20,
        max_length=64,
        description="Token contract/mint address",
        examples=["0x1234567890abcdef1234567890abcdef12345678"],
    )
    chain: str = Field(
        default="solana",
        description="Blockchain (solana, ethereum, bsc, base, polygon, arbitrum, optimism)",
        examples=["solana"],
    )
    use_reranker: bool = Field(
        default=True,
        description="Use cross-encoder reranker for enhanced scoring accuracy",
    )
    token_data: dict[str, Any] | None = Field(
        default=None,
        description="Optional pre-gathered token data for scoring",
    )


class RateTokenResponse(BaseModel):
    """Response with full rating breakdown."""

    success: bool = True
    rating: dict[str, Any]
    deployer_info: dict[str, Any] | None = None
    wallet_labels: list[dict[str, Any]] | None = None


class DeployerProfileResponse(BaseModel):
    """Deployer profile response."""

    success: bool = True
    profile: dict[str, Any] | None = None
    is_factory: bool = False
    real_deployer: str | None = None


class WalletLabelsResponse(BaseModel):
    """Wallet labels response."""

    success: bool = True
    address: str
    chain: str
    labels: list[dict[str, Any]]
    is_scammer: bool = False
    is_good_actor: bool = False
    deployer_profile: dict[str, Any] | None = None


# ── POST /api/v1/scoring/rate ───────────────────────────────────────


@router.post("/rate", response_model=RateTokenResponse)
async def rate_token(req: RateTokenRequest) -> dict[str, Any]:
    """
    Rate a token using the multi-factor scoring algorithm.

    Returns a 0-100 safety score with full breakdown across 5 categories:
    Contract Safety, Liquidity Safety, Holder Analysis, Deployer Reputation,
    and Trading Activity. Optionally enhanced by the cross-encoder reranker.

    Also resolves the real deployer (bypassing factory addresses like
    pump.fun) and returns wallet labels.
    """
    try:
        from app.scoring.deployer_tracker import DeployerTracker
        from app.scoring.token_rater import compute_rating

        logger.info("rate_token_request", address=req.address[:12], chain=req.chain)

        # ── 1. Rate the token ──
        rating = await compute_rating(
            token_address=req.address,
            chain=req.chain,
            token_data=req.token_data,
            use_reranker=req.use_reranker,
        )

        # ── 2. Resolve real deployer ──
        deployer_info: dict[str, Any] | None = None
        claimed_deployer: str | None = None
        if req.token_data:
            dev = req.token_data.get("dev_reputation", req.token_data.get("deployer", {}))
            if isinstance(dev, dict):
                val = dev.get("deployer_address", dev.get("address"))
                if isinstance(val, str):
                    claimed_deployer = val

        if claimed_deployer:
            try:
                tracker = DeployerTracker()
                resolved = await tracker.track(
                    token_address=req.address,
                    chain=req.chain,
                    deployer_address=claimed_deployer,
                    token_data=req.token_data,
                )
                deployer_info = resolved.to_dict()
                deployer_info["_resolution"] = {
                    "real_deployer": resolved.real_deployer_address or resolved.address,
                    "is_factory": resolved.is_factory,
                    "factory_name": resolved.factory_name,
                }
            except Exception as exc:
                logger.warning("deployer_resolve_failed_in_rate", error=str(exc)[:100])
                deployer_info = None

        # ── 3. Get wallet labels ──
        wallet_labels: list[dict[str, Any]] | None = None
        if claimed_deployer:
            try:
                from app.scoring.wallet_labeler import WalletLabeler

                labeler = WalletLabeler()
                labels_result = await labeler.get_deployer_labels(req.chain, claimed_deployer)
                wallet_labels = labels_result.get("labels", [])
            except Exception as exc:
                logger.warning("wallet_labels_failed_in_rate", error=str(exc)[:100])

        return {
            "success": True,
            "rating": rating.to_dict(),
            "deployer_info": deployer_info,
            "wallet_labels": wallet_labels,
        }

    except ValueError as exc:
        logger.warning("rate_token_value_error", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("rate_token_failed", error=str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Rating failed: {str(exc)[:200]}") from exc


# ── GET /api/v1/scoring/deployer/{chain}/{address} ─────────────────


@router.get("/deployer/{chain}/{address}", response_model=DeployerProfileResponse)
async def get_deployer(chain: str, address: str) -> dict[str, Any]:
    """
    Get deployer profile for a wallet address.

    Detects if the address is a factory/launchpad (e.g. pump.fun) and,
    if so, attempts to resolve the real creator.
    """
    try:
        from app.scoring.deployer_tracker import (
            get_deployer_profile,
            resolve_deployer,
        )

        logger.info("deployer_profile_request", chain=chain, address=address[:12])

        # Resolve if factory
        resolved = await resolve_deployer(
            token_address="",  # No token context for profile lookup
            chain=chain,
            deployer_address=address,
        )

        is_factory = resolved.get("is_factory", False)
        real_deployer = resolved.get("real_deployer") if is_factory else None

        # Get profile (graceful on Redis failure)
        lookup_address = real_deployer if real_deployer and is_factory else address
        try:
            profile = await get_deployer_profile(chain, lookup_address)
            profile_dict = profile.to_dict() if profile else None
        except Exception as exc:
            logger.debug("deployer_profile_redis_unavailable", error=str(exc)[:80])
            profile_dict = None

        return {
            "success": True,
            "profile": profile_dict,
            "is_factory": is_factory,
            "real_deployer": real_deployer,
        }

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("deployer_profile_failed", error=str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)[:200]) from exc


# ── GET /api/v1/scoring/wallet/{chain}/{address} ───────────────────


@router.get("/wallet/{chain}/{address}", response_model=WalletLabelsResponse)
async def get_wallet(chain: str, address: str) -> dict[str, Any]:
    """
    Get labels and profile for a wallet address.

    Returns all known labels (exchange, bot, sniper, whale, dev, rugger,
    MEV, etc.), scammer/good-actor status, and deployer profile if the
    wallet has deployed tokens.
    """
    try:
        from app.scoring.wallet_labeler import WalletLabeler

        logger.info("wallet_labels_request", chain=chain, address=address[:12])

        labeler = WalletLabeler()
        result = await labeler.get_deployer_labels(chain, address)

        return {
            "success": True,
            "address": address,
            "chain": chain,
            "labels": result.get("labels", []),
            "is_scammer": result.get("is_scammer", False),
            "is_good_actor": result.get("is_good_actor", False),
            "deployer_profile": result.get("deployer_profile"),
        }

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("wallet_labels_failed", error=str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)[:200]) from exc
