"""Block renderers for the premium card — one function per block, pure layout.

The aesthetic is a terminal blotter inside a chat: monospace panels, aligned
columns, one accent word per line. Telegram gives one mechanism for alignment —
``<pre>`` — so every panel is a ``<pre>`` block and every line stays under 34
characters so a phone preview never scrolls sideways.

Every block is written twice: ``html=True`` emits tags, ``html=False`` emits the
identical layout as plain text. The plain-text body is the fallback Telegram
gets when the parser rejects the markup, so it is a second rendering, not a
tag-strip.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from app.domains.telegram.premium.numbers import count, money, move, pct, price, short, sol

if TYPE_CHECKING:
    from app.domains.telegram.premium.model import CardData
PANEL_WIDTH = 34
BAR = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _b(text: str, html: bool) -> str:
    return f"<b>{text}</b>" if html else text


def _i(text: str, html: bool) -> str:
    return f"<i>{text}</i>" if html else text


def _panel(lines: list[str], html: bool) -> str:
    """A monospace panel; tags are not allowed inside (Telegram parses none)."""
    body = "\n".join(lines)
    return f"<pre>{escape(body, quote=False)}</pre>" if html else body


def _kv(label: str, value: str, width: int = 13) -> str:
    """Left label, left value — the reading pattern for the free card."""
    return f"{label:<{width}}{value}"


def _authority(value: str | None, html: bool) -> str:
    if value is None:
        return "unknown"
    if value == "revoked":
        return "revoked"
    return f"active  {short(value)}"


def _bar(severity: str) -> str:
    rank = BAR.get(severity, 0)
    return "▮" * rank + "▯" * (4 - rank)


def header(d: CardData, html: bool) -> list[str]:
    """Severity bar, ticker, chain — the line that decides whether to read on."""
    score = f" · {d.safety:.0f}/100" if d.safety is not None else ""
    return [
        _b(f"{_bar(d.severity)} {d.severity} · ${escape(d.symbol, quote=False)} · {d.chain}{score}", html),
        f"<code>{escape(d.address)}</code>" if html else d.address,
    ]


def meta(d: CardData, html: bool) -> list[str]:
    bits = [d.scanned_at or "unscheduled scan"]
    if d.duration_ms:
        bits.append(f"{d.duration_ms / 1000:.1f}s")
    bits.append(f"source {d.source}")
    return [_i(" · ".join(bits), html)]


def verdict(d: CardData, html: bool) -> list[str]:
    """What it is, what drives it, what to do — three lines, never truncated."""
    safety = f"{d.safety:.0f}/100 safety" if d.safety is not None else "safety unknown"
    determination = d.determination or "no determination"
    driver = d.driver or "No graded risk driver surfaced"
    return [
        f"{_b('Verdict', html)} {safety} · {escape(determination, quote=False)}",
        f"{_b('Driver', html)} {escape(driver, quote=False)}",
        f"{_b('Action', html)} {escape(d.action, quote=False)}",
    ]


def core(d: CardData, html: bool) -> list[str]:
    """The three numbers every tier keeps: liquidity, top-10, safety."""
    return [
        _panel(
            [
                _kv("Liquidity", money(d.liquidity_usd)),
                _kv("Top-10", pct(d.top10_pct)),
                _kv("Safety", f"{d.safety:.0f}/100" if d.safety is not None else "unknown"),
            ],
            html,
        )
    ]


def cohort(d: CardData, html: bool) -> list[str]:
    """Launch cohorts: what they bought, what they sold, what they banked."""
    rows = [f"{'':<11}{'init':>7}{'sold':>7}{'realised':>9}"]
    for c in d.cohorts:
        realised = f"{c.realised_sol:+.2f}" if c.realised_sol is not None else "—"
        if c.estimated and c.realised_sol is not None:
            realised = f"{realised}*"
        name = f"{c.name} {c.wallets}w" if c.wallets else c.name
        rows.append(f"{name:<11}{sol(c.initial_sol):>7}{pct(c.sold_pct):>7}{realised:>9}")
    return [_panel(rows, html)]


def supply(d: CardData, html: bool) -> list[str]:
    """Supply concentration: bundle share, top-10, dev bag."""
    wallets = f" ({d.bundle_wallets}w)" if d.bundle_wallets else ""
    rows = [_kv("Bundle", f"{pct(d.bundle_pct)}{wallets}"), _kv("Top-10", pct(d.top10_pct))]
    if d.dev_holdings_pct is not None:
        rows.append(_kv("Dev bag", pct(d.dev_holdings_pct)))
    if d.holders:
        rows.append(_kv("Holders", count(d.holders)))
    return [_panel(rows, html)]


def market(d: CardData, html: bool) -> list[str]:
    """Liquidity, LP state, and the notional that moves price 1/5/10%."""
    rows = [
        _kv("Liquidity", money(d.liquidity_usd)),
        _kv(
            "LP",
            f"{d.lp_state} ({pct(d.lp_locked_pct)})" if d.lp_locked_pct is not None else d.lp_state,
        ),
        _kv("Vol 24h", money(d.volume_24h)),
        _kv("Price", f"{price(d.price_usd)} ({move(d.change_24h)})"),
    ]
    for cut, notional in d.impact_depth.items():
        rows.append(_kv(f"±{cut} move", money(notional)))
    return [_panel(rows, html)]


def authorities(d: CardData, html: bool) -> list[str]:
    """Mint and freeze authority — the two switches that end a token."""
    rows = [
        _kv("Mint", _authority(d.mint_authority, html)),
        _kv("Freeze", _authority(d.freeze_authority, html)),
    ]
    return [_panel(rows, html)]


def dev(d: CardData, html: bool) -> list[str]:
    """Deployer history: how many tokens, how many rugs, flagged or not."""
    scammer = {True: "yes", False: "no", None: "unknown"}[d.dev_scammer]
    rows = [
        _kv("Prior tokens", count(d.dev_tokens)),
        _kv("Prior rugs", count(d.dev_rugs)),
        _kv("Scammer", scammer),
    ]
    return [_panel(rows, html)]


def kol(d: CardData, html: bool) -> list[str]:
    """Known-opinion-leader holders. The scanner does not produce this yet."""
    return [_panel([_kv("KOLs", ", ".join(d.kols) if d.kols else "no data")], html)]


def flags(d: CardData, html: bool) -> list[str]:
    """Named flags, status first so severity survives a glance."""
    out = [_b("Flags", html)]
    out.extend(f"• [{escape(status, quote=False)}] {escape(reason, quote=False)}" for reason, status in d.flags)
    if not d.flags:
        out.append("None recorded.")
    if d.flags_dropped:
        out.append(_i(f"… {d.flags_dropped} more flag(s) in the full report", html))
    return out


def flag1(d: CardData, html: bool) -> list[str]:
    """Free tier: the single driver, stated once."""
    if not d.flags:
        return []
    reason, status = d.flags[0]
    extra = f" (+{d.flags_dropped} more)" if d.flags_dropped else ""
    return [f"{_b('Flag', html)} [{escape(status, quote=False)}] {escape(reason, quote=False)}{extra}"]


def legend(d: CardData, html: bool) -> list[str]:
    """Say what the symbols mean; do not make the reader infer them."""
    return [
        _i(
            "— no data · * realised estimated from wallet PnL · ± move = notional for that price move (CPMM)",
            html,
        )
    ]


def link(d: CardData, html: bool) -> list[str]:
    """Exactly one destination, named."""
    if html:
        return [f'<a href="{escape(d.link_url)}">{d.link_label} ↗</a>']
    return [f"{d.link_label}: {d.link_url}"]


BLOCKS = {
    "header": header,
    "meta": meta,
    "verdict": verdict,
    "core": core,
    "cohort": cohort,
    "supply": supply,
    "market": market,
    "authorities": authorities,
    "dev": dev,
    "kol": kol,
    "flags": flags,
    "flag1": flag1,
    "legend": legend,
    "link": link,
}


def block(name: str, d: CardData, html: bool = True) -> list[str]:
    """Render one named block. Unknown names are a programming error, not data."""
    return BLOCKS[name](d, html)
