"""Assemble a tier's card: HTML for Telegram, plain text for the retry path.

``render_card`` is the only entry point. It composes the blocks named by the
tier, and when the body exceeds the cap it applies the prune plan from
:mod:`prune` — re-rendering after each step, so no tag is ever cut in half and
the header, verdict, core numbers and link survive every step.
"""

from __future__ import annotations

from app.domains.telegram.premium import sections
from app.domains.telegram.premium.model import CardData, RenderedCard
from app.domains.telegram.premium.prune import prune_plan
from app.domains.telegram.premium.tiers import tier as resolve_tier

MAX_CHARS = 4096


def _compose(d: CardData, names: tuple[str, ...], html: bool) -> str:
    """Header, blank line, then each block separated by one blank line."""
    lines = sections.block("header", d, html)
    out = [*lines, ""]
    for name in names:
        block = sections.block(name, d, html)
        if block:
            out.extend(block)
            out.append("")
    return "\n".join(out).rstrip()


def render_card(
    data: CardData, tier: str = "free", prev: CardData | None = None, max_chars: int = MAX_CHARS
) -> RenderedCard:
    """Render ``data`` for a tier within the Telegram hard cap."""
    spec = resolve_tier(tier)
    if spec.name == "alert":
        from app.domains.telegram.premium.delta import render_alert

        return render_alert(data, prev, max_chars)
    body = _compose(data, spec.sections, True)
    applied: list[str] = []
    current, names = data, spec.sections
    for step in prune_plan(spec.name):
        if len(body) <= max_chars:
            break
        applied.append(step.name)
        names = tuple(n for n in names if n != step.drop)
        current = step.apply(current) if step.apply else current
        body = _compose(current, names, True)
    if len(body) > max_chars:
        # Last resort: the four things a card cannot be a card without.
        names = tuple(n for n in ("verdict", "core", "link") if n in spec.sections)
        current, applied = current.replace(flags=()), [*applied, "last_resort"]
        body = _compose(current, names, True)
    if len(body) > max_chars:
        raise ValueError(f"card exceeds {max_chars} chars without losing the verdict")
    return RenderedCard(html=body, text=_compose(current, names, False), pruned=tuple(applied))
