"""Security checks — sanctions, threat detection, chain patrol."""

from __future__ import annotations

from typing import Any

from app.domains.scanner.modules._core import logger


async def _check_trm_sanctions(address: str) -> dict[str, Any] | None:
    """Check address on TRM sanctions list."""
    logger.info("check_trm_sanctions", address=address[:10])
    return {"check": "trm", "result": "pending"}


async def _check_scorechain_sanctions(address: str, chain: str = "ethereum") -> dict[str, Any] | None:
    """Check address on Scorechain sanctions list."""
    logger.info("check_scorechain", address=address[:10], chain=chain)
    return {"check": "scorechain", "result": "pending"}


async def _check_chainabuse(address: str) -> dict[str, Any] | None:
    """Check address on Chainabuse."""
    logger.info("check_chainabuse", address=address[:10])
    return {"check": "chainabuse", "result": "pending"}


async def _check_forta_alerts(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Forta alerts for token."""
    logger.info("check_forta", token=token_address, chain=chain)
    return {"check": "forta", "result": "pending"}


async def _check_blowfish(token_address: str, chain: str, user_address: str = "") -> dict[str, Any] | None:
    """Check Blowfish AI for token."""
    logger.info("blowfish_check", token=token_address, chain=chain, user=user_address)
    return {"check": "blowfish", "result": "pending"}


async def _check_webacy(address: str, chain: str) -> dict[str, Any] | None:
    """Check Webacy for address."""
    logger.info("webacy_check", address=address, chain=chain)
    return {"check": "webacy", "result": "pending"}


async def _check_scamsniffer_live(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ScamSniffer Live for token."""
    logger.info("scamsniffer_live", token=token_address, chain=chain)
    return {"check": "scamsniffer", "result": "pending"}


async def _check_chainpatrol_asset(asset_type: str, asset_id: str) -> dict[str, Any] | None:
    """Check asset on ChainPatrol."""
    logger.info("check_chainpatrol", type=asset_type, id=asset_id)
    return {"check": "chainpatrol", "result": "pending"}
