"""Launch-watch collector: ``TokenLaunchDetector`` output -> canonical launch alerts.

Pulls recent token launches from ``TokenLaunchDetector.get_recent_launches``
and normalizes each raw launch dict into the canonical ``PlatformAlert`` shape
(category ``launch``, severity ``high``) so the alerts pipeline can route them.
``publisher.DEFAULT_CHANNELS`` sends launch alerts to ``rmialpha`` (highest-
signal early data, high+) and ``rmicryptonews`` (news categories).

Best-effort by design: any detector failure returns ``[]`` (fail-open) and a
single malformed launch never aborts the batch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.domains.alerts.models import PlatformAlert, PlatformAlertSeverity

if TYPE_CHECKING:
    from app.databus.token_launch_detector import TokenLaunchDetector

logger = get_logger("alerts.launch_watch")

#: Source label for launch-watch alerts (distinct from the raw detector source).
SOURCE = "launch_detector"

#: How many recent launches to pull per collection cycle.
LAUNCH_LIMIT = 50


def _fmt_usd(value: Any) -> str:
    """Format a USD amount compactly (rounded above $100, precise below)."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if amount >= 100:
        return f"${amount:,.0f}"
    return f"${amount:.4f}"


def _fmt_age(seconds: Any) -> str:
    """Format an age in seconds as a compact human-readable string."""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "unknown"
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60}s"
    return f"{total // 3600}h {(total % 3600) // 60}m"


def _normalize_launch(raw: dict[str, Any]) -> PlatformAlert | None:
    """Map one ``get_recent_launches`` dict into a canonical launch alert.

    Detector shape (see ``app/databus/token_launch_detector.py``):
    address, chain, symbol, name, price, liquidity, market_cap, created_at,
    age_seconds, dex, pair_address, source (``discovered_at`` added on store).
    Returns ``None`` for launches with no address or an unknown symbol.
    """
    token = (raw.get("address") or "").strip()
    symbol = (raw.get("symbol") or "").strip()
    chain = (raw.get("chain") or "solana").strip()
    if not token or not symbol or symbol == "???":
        return None

    title = f"New launch: {symbol} ({chain})"
    parts: list[str] = []
    if raw.get("pair_address"):
        parts.append(f"Pair: {raw.get('pair_address')}")
    parts.append(f"Address: {token}")
    parts.append(f"Initial liquidity: {_fmt_usd(raw.get('liquidity'))}")
    parts.append(f"Age: {_fmt_age(raw.get('age_seconds'))}")
    if raw.get("dex"):
        parts.append(f"DEX: {raw.get('dex')}")

    return PlatformAlert(
        severity=PlatformAlertSeverity.HIGH,
        category="launch",
        title=title,
        body="\n".join(parts),
        token_address=token,
        chain=chain,
        source=SOURCE,
        confidence=0.7,
    )


async def collect_launch_alerts(detector: TokenLaunchDetector | None = None) -> list[PlatformAlert]:
    """Collect recent token launches as canonical ``PlatformAlert`` objects.

    Pulls ``get_recent_launches`` off the detector (the singleton when
    ``detector`` is None) and normalizes each raw launch into a
    ``category="launch", severity="high"`` alert. Fail-open: any detector
    error returns ``[]``; a malformed item is skipped without aborting.
    """
    if detector is None:
        from app.databus.token_launch_detector import get_launch_detector

        detector = get_launch_detector()

    try:
        raw_launches = await detector.get_recent_launches(limit=LAUNCH_LIMIT)
    except Exception as exc:  # fail-open by contract
        logger.warning("launch_watch_collect_failed: %s", exc)
        return []

    alerts: list[PlatformAlert] = []
    for raw in raw_launches or []:
        try:
            alert = _normalize_launch(raw)
        except Exception as exc:  # best-effort per item
            logger.warning("launch_watch_item_failed: %s", exc)
            continue
        if alert is not None:
            alerts.append(alert)
    return alerts
