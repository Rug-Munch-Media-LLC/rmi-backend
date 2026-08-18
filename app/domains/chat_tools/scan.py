"""Scan executor for the chat tool-use layer.

Best-effort async executor that runs the scanner entrypoint and returns a
rich token risk summary with structured data. Fail-open: returns ``None``
on any error/timeout and NEVER raises.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone

from app.core.logging import get_logger

logger = get_logger(__name__)


async def _exec_scan(message: str) -> str | None:
    """Run the scanner entrypoint and return a rich risk summary or None."""
    from app.domains.chat_tools import _chain_for_address, _extract_first_address

    address = _extract_first_address(message)
    if not address:
        return None
    chain = _chain_for_address(address)

    try:
        from app.domains.scanner.service import scan as _scan_svc

        result = await _scan_svc.scan_token(address, chain=chain, tiers=["free"])
    except Exception as e:
        logger.debug("chat_tool_scan_exec_failed", error=str(e)[:120])
        return None

    try:
        safety = float(getattr(result, "safety_score", 0) or 0)
        risk = (_scan_svc._risk_level_from_safety(safety) or "UNKNOWN").upper()
        flags = [str(f) for f in (getattr(result, "risk_flags", None) or [])][:8]
        holders = getattr(result, "holders", None) or getattr(result, "holder_count", None) or "?"
        liquidity = getattr(result, "liquidity", None) or getattr(result, "liquidity_usd", None) or "?"
        market_cap = getattr(result, "market_cap", None) or getattr(result, "mcap", None) or "?"
        volume_24h = getattr(result, "volume_24h", None) or "?"
        is_honeypot = getattr(result, "is_honeypot", False) or getattr(result, "honeypot", False)
        is_mintable = getattr(result, "is_mintable", False) or getattr(result, "mintable", False)
        owner_renounced = getattr(result, "owner_renounced", None)
        top10_pct = getattr(result, "top_10_holder_pct", None) or getattr(result, "top10_pct", None)
        created_at = getattr(result, "created_at", None) or getattr(result, "age", None) or "?"
    except Exception as e:
        logger.debug("chat_tool_scan_attr_failed err=%s", e)
        return _fallback_summary(address, chain, result)

    # Build a structured risk summary
    risk_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢", "UNKNOWN": "⚪"}
    emoji = risk_emoji.get(risk, "⚪")

    lines = [
        f"Token Scan: `{address[:18]}…` on **{chain}**",
        f"Safety Score: **{safety:.0f}/100** {emoji} | Risk: **{risk}**",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]

    def _v(val: Any) -> str:
        if val is None or val == "?":
            return "?"
        if isinstance(val, float):
            return f"{val:,.2f}" if val > 1 else f"{val:.4f}"
        return str(val)

    lines.append(f"| Holders | {_v(holders)} |")
    lines.append(f"| Liquidity | ${_v(liquidity)} |" if liquidity != "?" else "| Liquidity | ? |")
    lines.append(f"| Market Cap | ${_v(market_cap)} |" if market_cap != "?" else "| Market Cap | ? |")
    lines.append(f"| 24h Volume | ${_v(volume_24h)} |" if volume_24h != "?" else "| 24h Volume | ? |")

    if flags:
        lines.append(f"| Risk Flags | {', '.join(flags[:5])} |")

    if is_honeypot:
        lines.append(f"| ❌ Honeypot | **YES** — cannot sell |")
    if is_mintable:
        lines.append(f"| ⚠️ Mintable | Supply can be inflated |")
    if owner_renounced is not None:
        lines.append(f"| Owner Renounced | {'✅ Yes' if owner_renounced else '❌ No'} |")
    if top10_pct is not None:
        lines.append(f"| Top 10 Holders | {_v(top10_pct)}% of supply |")

    lines.append(f"| Token Age | {_v(created_at)} |")
    lines.append(f"| Scan Time | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} |")

    # Risk breakdown
    if safety <= 30:
        lines.append("\n**⚠️ HIGH RISK** — Strong indicators of malicious behavior. Avoid interacting with this token.")
    elif safety <= 50:
        lines.append(
            "\n**⚠️ MEDIUM RISK** — Some suspicious patterns detected. Proceed with caution and verify before investing."
        )
    elif safety <= 70:
        lines.append("\n**✅ LOW RISK** — Minor concerns. Reasonable safety profile.")
    else:
        lines.append("\n**✅ SAFE** — Clean security profile. Standard token behavior.")

    return textwrap.dedent("\n".join(lines))


def _fallback_summary(address: str, chain: str, result: object) -> str:
    """Fallback when structured attributes are unavailable."""
    safety = float(getattr(result, "safety_score", 0) or 0)
    return textwrap.dedent(f"""\
    Token: `{address[:18]}…` on {chain}
    Safety Score: {safety:.0f}/100
    Risk Level: {"HIGH" if safety < 40 else "MEDIUM" if safety < 70 else "LOW"}
    Note: Limited data available — run a full scan for complete analysis.
    """)


from typing import Any  # noqa: E402 — late import for type hint
