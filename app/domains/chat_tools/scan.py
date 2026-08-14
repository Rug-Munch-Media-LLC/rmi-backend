"""Scan executor for the chat tool-use layer.

Best-effort async executor that runs the scanner entrypoint and returns a
compact risk summary. Fail-open: returns ``None`` on any error/timeout and
NEVER raises.
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


async def _exec_scan(message: str) -> str | None:
    """Run the scanner entrypoint and return a compact risk summary or None."""
    # Patch-sensitive shared helpers resolve through the package shim at call
    # time so tests can mock them on app.domains.chat_tools.* (lazy to avoid
    # an import cycle — the shim imports this module).
    from app.domains.chat_tools import _chain_for_address, _extract_first_address

    address = _extract_first_address(message)
    if not address:
        return None
    chain = _chain_for_address(address)
    try:
        from app.domains.scanner.service import scan as _scan_svc

        result = await _scan_svc.scan_token(address, chain=chain, tiers=["free"])
        safety = float(getattr(result, "safety_score", 0) or 0)
        risk = _scan_svc._risk_level_from_safety(safety) or "UNKNOWN"
        flags = [str(f) for f in (getattr(result, "risk_flags", None) or [])][:5]
        flags_str = ", ".join(flags) if flags else "None"
        return f"{address[:14]}… on {chain}: Safety score {safety:.0f}/100 ({risk}). Flags: {flags_str}."
    except Exception as e:
        logger.debug("chat_tool_scan_failed", error=str(e)[:120])
        return None
