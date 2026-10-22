"""Alert tier: the delta only, never the whole card again.

An alert is a claim about change, so it renders only what moved. If there is no
prior snapshot the card says so rather than inventing a delta — a fabricated
"cashed out" alert is worse than no alert at all.

Events are produced from two :class:`CardData` snapshots of the same token; the
thresholds live here so a rule change is one diff, not a hunt.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from app.domains.telegram.premium import sections
from app.domains.telegram.premium.model import CardData, RenderedCard
from app.domains.telegram.premium.numbers import money, move, pct, short, sol

COHORT_EXIT_PCT = 15.0
TOP10_SHIFT_PCT = 5.0
MAX_EVENTS = 3
LOCKED = ("locked", "burned")


@dataclass(frozen=True)
class AlertEvent:
    """One line of change: what happened, how much, which side."""

    headline: str
    detail: str


def _cohort_exit(prev: CardData, curr: CardData) -> list[AlertEvent]:
    events = []
    for before, after in zip(prev.cohorts, curr.cohorts, strict=False):
        if before.name != after.name or before.sold_pct is None or after.sold_pct is None:
            continue
        if after.sold_pct - before.sold_pct < COHORT_EXIT_PCT:
            continue
        realised = f"est +{sol(after.realised_sol)} SOL realised · " if after.realised_sol else ""
        events.append(
            AlertEvent(
                f"{after.name} exited {pct(after.sold_pct)} of the bag",
                f"{realised}price {move(curr.change_24h)}",
            )
        )
    return events


def _bundle_growth(prev: CardData, curr: CardData) -> list[AlertEvent]:
    grew = (curr.bundle_wallets or 0) - (prev.bundle_wallets or 0)
    if grew < 1:
        return []
    return [
        AlertEvent(
            f"{grew} new bundle wallet{'s' if grew > 1 else ''} on the supply",
            f"{curr.bundle_wallets} wallets now hold {pct(curr.bundle_pct)}",
        )
    ]


def _lp_unlock(prev: CardData, curr: CardData) -> list[AlertEvent]:
    if prev.lp_state not in LOCKED or curr.lp_state in LOCKED:
        return []
    return [AlertEvent("LP unlocked", f"{money(curr.liquidity_usd)} now withdrawable")]


def _authority(prev: CardData, curr: CardData) -> list[AlertEvent]:
    events = []
    for label, before, after in (
        ("Freeze", prev.freeze_authority, curr.freeze_authority),
        ("Mint", prev.mint_authority, curr.mint_authority),
    ):
        if before == "revoked" and after not in (None, "revoked"):
            events.append(AlertEvent(f"{label} authority re-armed", short(after)))
    return events


def _top10(prev: CardData, curr: CardData) -> list[AlertEvent]:
    if prev.top10_pct is None or curr.top10_pct is None:
        return []
    delta = curr.top10_pct - prev.top10_pct
    if abs(delta) < TOP10_SHIFT_PCT:
        return []
    return [AlertEvent(f"Top-10 concentration {move(delta)}", f"now {pct(curr.top10_pct)} of supply")]


def alert_events(prev: CardData, curr: CardData) -> tuple[AlertEvent, ...]:
    """All detected changes, largest signal first, capped for a notification."""
    found = (
        _cohort_exit(prev, curr)
        + _authority(prev, curr)
        + _lp_unlock(prev, curr)
        + _bundle_growth(prev, curr)
        + _top10(prev, curr)
    )
    return tuple(found[:MAX_EVENTS])


def render_alert(curr: CardData, prev: CardData | None, max_chars: int = 4096) -> RenderedCard:
    """Render the alert tier: delta plus one link to the full scan."""
    events = alert_events(prev, curr) if prev is not None else ()
    head = sections.block("header", curr, True)
    head[0] = head[0].replace("</b>", " · ALERT</b>")
    out = [*head, ""]
    if prev is None:
        out += [
            "<b>No prior snapshot</b>",
            "First scan for this token — nothing to compare, so nothing to alert on.",
            "",
        ]
    elif events:
        for event in events:
            out += [f"<b>{escape(event.headline)}</b>", escape(event.detail), ""]
    else:
        out += [
            "<b>No material change</b>",
            "No cohort exit, bundle growth, LP or authority move since the last scan.",
            "",
        ]
    out += sections.block("link", curr, True)
    out += ["<i>alert card · delta only</i>"]
    html = "\n".join(out).rstrip()
    text = _alert_text(curr, events)
    if len(html) > max_chars:
        html = "\n".join([*head, "", sections.block("link", curr, True)[0]])
        text = html
    return RenderedCard(html=html, text=text, pruned=("alert_delta_only",))


def _alert_text(curr: CardData, events: tuple[AlertEvent, ...]) -> str:
    out = [f"{curr.severity} · ${curr.symbol} · ALERT", curr.address, ""]
    for event in events:
        out += [event.headline, event.detail, ""]
    if not events:
        out += ["No material change since the last scan.", ""]
    out += [f"{curr.link_label}: {curr.link_url}", "alert card · delta only"]
    return "\n".join(out)
