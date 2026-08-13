"""
Intelligence Router - Cards & Marketing
=========================================
Card generation and marketing graphics endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.errors import AppError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ── Card Generation Endpoints ─────────────────────────────────


@router.post("/cards/generate")
async def generate_card(card_type: str, data: dict[str, Any]) -> Any:
    """Generate shareable card (win, loss, KOL, rug, etc.)."""
    try:
        from app.card_generator import (
            generate_kol_scorecard,
            generate_loss_card,
            generate_rug_call_card,
            generate_smart_money_card,
            generate_whale_alert_card,
            generate_win_card,
        )

        generators: dict[str, Any] = {
            "big_win": generate_win_card,
            "big_loss": generate_loss_card,
            "kol_scorecard": generate_kol_scorecard,
            "rug_call": generate_rug_call_card,
            "whale_move": generate_whale_alert_card,
            "smart_money": generate_smart_money_card,
        }

        generator = generators.get(card_type)
        if not generator:
            raise ValidationError(f"Unknown card type: {card_type}")

        result = await generator(**data)
        return result

    except (HTTPException, AppError):
        raise
    except Exception as e:
        logger.error("Card generation failed: %s", e)
        raise AppError(message=str(e)[:200]) from e


@router.get(
    "/cards/templates",
    summary="Get Card Templates",
    description="List available intelligence card templates for generating shareable security/market insight graphics.",
)
async def get_card_templates() -> dict[str, list[dict[str, Any]]]:
    """Get available card templates."""
    try:
        from typing import cast

        from app.card_generator import CARD_TEMPLATES

        templates: list[dict[str, Any]] = []
        for key, template in CARD_TEMPLATES.items():
            tpl = cast("dict[str, Any]", template)
            templates.append(
                {
                    "id": key,
                    "name": tpl["name"],
                    "size": tpl["size"],
                    "colors": tpl["colors"],
                }
            )

        return {"templates": templates}
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


# ── Marketing Graphics Endpoints ─────────────────────────────


@router.get("/marketing/templates")
async def list_marketing_templates_endpoint() -> dict[str, Any]:
    """List available marketing graphic templates."""
    try:
        from app.marketing_graphics_generator import list_marketing_templates

        return {"templates": list_marketing_templates()}
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


@router.post("/marketing/generate")
async def generate_marketing_graphic(graphic_type: str, data: dict[str, Any] | None = None) -> Any:
    """Generate marketing graphic (banner, feature card, stats, etc.)."""
    try:
        from app.marketing_graphics_generator import generate_marketing_graphic

        result = generate_marketing_graphic(graphic_type, data)

        if result.get("status") == "error":
            raise AppError(message=str(result.get("error") or "generation failed"))

        return result

    except (HTTPException, AppError):
        raise
    except Exception as e:
        logger.error("Marketing graphic generation failed: %s", e)
        raise AppError(message=str(e)[:200]) from e


@router.post("/marketing/generate/feature-cards")
async def generate_all_feature_cards_endpoint() -> dict[str, Any]:
    """Generate feature showcase cards for all features."""
    try:
        from app.marketing_graphics_generator import generate_all_feature_cards

        results = await generate_all_feature_cards()
        return {"cards": results, "count": len(results)}
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


@router.post(
    "/marketing/generate/stats",
    summary="Generate Stats Graphic",
    description="Generate a stats/metrics marketing graphic from the provided stat value, label, and context. Returns a rendered image URL.",
)
async def generate_stats_graphic(stat_value: str, stat_label: str, context: str = "") -> Any:
    """Generate stats/metrics graphic."""
    try:
        from app.marketing_graphics_generator import generate_stats_card

        result = await generate_stats_card(stat_value, stat_label, context)

        if result.get("status") == "error":
            raise AppError(message=str(result.get("error") or "generation failed"))

        return result

    except (HTTPException, AppError):
        raise
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


@router.post(
    "/marketing/generate/pricing",
    summary="Generate Pricing Graphic",
    description="Generate a pricing tier marketing graphic with the given tier name, price, and feature list. Returns a rendered image URL.",
)
async def generate_pricing_graphic(tier_name: str, price: float, features: list[str]) -> Any:
    """Generate pricing tier graphic."""
    try:
        from app.marketing_graphics_generator import generate_pricing_card

        result = await generate_pricing_card(tier_name, price, features)

        if result.get("status") == "error":
            raise AppError(message=str(result.get("error") or "generation failed"))

        return result

    except (HTTPException, AppError):
        raise
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e
