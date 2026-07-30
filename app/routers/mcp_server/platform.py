"""Platform manifest, agent skills, membership plans."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger("rmi_mcp_v3")
router = APIRouter(tags=["mcp_platform"])


@router.get("/mcp/manifest")
async def platform_manifest() -> Any:
    """Complete platform manifest - auto-generated from live tool counts."""
    from app.caching_shield.platform_manifest import get_platform_manifest

    return get_platform_manifest()


@router.get("/mcp/skills")
async def agent_skills() -> Any:
    """Agent skills, workflows, anti-abuse rules, and starter prompts."""
    from app.caching_shield.agent_skills import get_agent_skills

    return get_agent_skills()


@router.get("/mcp/membership")
async def membership_plans() -> Any:
    """Membership tiers, scan packs, streams, research, batch processing."""
    from app.caching_shield.membership_plans import get_membership_catalog

    return get_membership_catalog()
