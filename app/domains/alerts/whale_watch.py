"""Whale-watch collector: whale moves -> canonical whale PlatformAlerts.

Pulls recent whale moves off an injectable watcher and normalizes each into
the canonical ``PlatformAlert`` shape (category ``whale``, severity ``high``,
title ``Whale: <amount> <token> moved``) so the alerts pipeline can route
them. ``publisher.DEFAULT_CHANNELS`` already sends whale alerts to
``rmialpha`` (highest-signal early data, high+) via ``ALPHA_CATEGORIES``.

Watcher resolution
------------------
The helius ``WhaleWatcher`` (``app/helius_tools/helius_whale_watcher.py``)
does NOT expose a public recent-alerts accessor today: it emits ``WhaleAlert``
dataclasses through ``register_alert_callback`` and keeps an internal
``_alert_history`` deque that is never populated by the shipped monitor loop.
The collector therefore resolves raw moves in this order:

  1. ``watcher.recent_alerts`` — an injectable list of WhaleAlert-shaped
     dicts (or ``WhaleAlert`` dataclass instances); a callable is invoked
     first. Tests and a future production wrapper (a callback that appends
     to a list) plug in here.
  2. ``watcher._alert_history`` — the real watcher's internal deque, for
     the day it is populated.
  3. Otherwise ``[]``.

Best-effort by design: any watcher failure returns ``[]`` (fail-open) and a
single malformed move never aborts the batch.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.domains.alerts.models import PlatformAlert, PlatformAlertSeverity

if TYPE_CHECKING:
    from app.helius_tools.helius_whale_watcher import WhaleWatcher

logger = get_logger("alerts.whale_watch")

#: Source label for whale-watch alerts (distinct from the ingestion canonical
#: ``"whale"`` source used by ``adapt_whale``).
SOURCE = "whale_watcher"


def _fmt_amount(value: Any) -> str:
    """Format a token amount compactly (whole amounts grouped, else 4 dp)."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if amount == int(amount):
        return f"{int(amount):,}"
    return f"{amount:,.4f}"


def _fmt_usd(value: Any) -> str:
    """Format a USD value with thousands separators."""
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "unknown"


def _short_addr(addr: str) -> str:
    """Shorten a long address for titles (``abc...xyz``)."""
    if len(addr) > 12:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


def _normalize_whale_move(raw: dict[str, Any]) -> PlatformAlert | None:
    """Map one WhaleAlert-shaped dict into a canonical whale alert.

    Accepts the ``WhaleAlert`` dataclass field names (``timestamp``,
    ``alert_type``, ``token_address``, ``token_symbol``, ``from_wallet``,
    ``to_wallet``, ``amount_tokens``, ``amount_usd``, ``pct_of_supply``,
    ``severity``, ``tx_signature``). Returns ``None`` when there is no
    token address to anchor the alert on.
    """
    token_address = (raw.get("token_address") or "").strip()
    if not token_address:
        return None

    symbol = (raw.get("token_symbol") or "").strip() or _short_addr(token_address)
    amount = _fmt_amount(raw.get("amount_tokens"))
    title = f"Whale: {amount} {symbol} moved"

    parts: list[str] = []
    if raw.get("from_wallet"):
        parts.append(f"From: {raw.get('from_wallet')}")
    if raw.get("to_wallet"):
        parts.append(f"To: {raw.get('to_wallet')}")
    parts.append(f"Amount: {amount} {symbol}")
    if raw.get("amount_usd") not in (None, ""):
        parts.append(f"USD value: {_fmt_usd(raw.get('amount_usd'))}")
    if raw.get("pct_of_supply") not in (None, ""):
        parts.append(f"% of supply: {raw.get('pct_of_supply')}%")
    if raw.get("tx_signature"):
        parts.append(f"Tx: {raw.get('tx_signature')}")

    ts = raw.get("timestamp") or raw.get("ts")
    extra: dict[str, Any] = {}
    if isinstance(ts, str) and ts.strip():
        extra["ts"] = ts.strip()
    return PlatformAlert(
        severity=PlatformAlertSeverity.HIGH,
        category="whale",
        title=title,
        body="\n".join(parts),
        token_address=token_address,
        chain=(raw.get("chain") or "solana").strip(),
        source=SOURCE,
        confidence=0.7,
        **extra,
    )


def _as_dict(item: Any) -> dict[str, Any]:
    """Coerce a raw move (dict or ``WhaleAlert`` dataclass) to a dict."""
    if isinstance(item, dict):
        return item
    if is_dataclass(item) and not isinstance(item, type):
        try:
            return asdict(item)
        except TypeError:
            return {}
    return {}


def _raw_whale_moves(watcher: Any) -> list[Any]:
    """Pull raw whale moves off a watcher, fail-open.

    Resolution order: ``watcher.recent_alerts`` (list or callable) ->
    ``watcher._alert_history`` (the real helius internal deque) -> ``[]``.
    """
    if watcher is None:
        return []
    recent = getattr(watcher, "recent_alerts", None)
    if callable(recent):
        try:
            recent = recent()
        except Exception as exc:  # fail-open by contract
            logger.warning("whale_watch_recent_failed: %s", exc)
            recent = None
    if isinstance(recent, (list, tuple, deque)):
        return list(recent)
    history = getattr(watcher, "_alert_history", None)
    if isinstance(history, (list, deque)):
        return list(history)
    return []


def collect_whale_alerts(watcher: WhaleWatcher | None = None) -> list[PlatformAlert]:
    """Collect recent whale moves as canonical ``PlatformAlert`` objects.

    Uses the injectable ``watcher`` when provided (tests / production
    wrapper); otherwise constructs a helius ``WhaleWatcher`` and reads its
    internal alert history (empty until a monitor loop populates it — the
    collector stays fail-open). Each raw move (dict or ``WhaleAlert``
    dataclass) is normalized to ``category="whale", severity="high",
    source="whale_watcher"``. Any watcher error returns ``[]``; a malformed
    item is skipped without aborting.
    """
    if watcher is None:
        from app.helius_tools.helius_whale_watcher import WhaleWatcher

        watcher = WhaleWatcher()

    try:
        raw_moves = _raw_whale_moves(watcher)
    except Exception as exc:  # fail-open by contract
        logger.warning("whale_watch_collect_failed: %s", exc)
        return []

    alerts: list[PlatformAlert] = []
    for raw in raw_moves or []:
        try:
            alert = _normalize_whale_move(_as_dict(raw))
        except Exception as exc:  # best-effort per item
            logger.warning("whale_watch_item_failed: %s", exc)
            continue
        if alert is not None:
            alerts.append(alert)
    return alerts
