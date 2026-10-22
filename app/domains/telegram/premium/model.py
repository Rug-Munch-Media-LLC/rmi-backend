"""Typed card data for the premium scan cards.

Every field is optional on purpose: the scanner fills a wide payload and a
quick scan leaves whole blocks empty. ``None`` means "not measured" and renders
as ``—`` (panel) or ``unknown`` (free tier), never as a zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Cohort:
    """One launch cohort: what it bought, what it still holds, what it banked."""

    name: str
    wallets: int | None = None
    initial_sol: float | None = None
    sold_pct: float | None = None
    realised_sol: float | None = None
    estimated: bool = False


@dataclass(frozen=True)
class CardData:
    """Everything one card may show, already normalised and unescaped."""

    address: str
    symbol: str
    chain: str
    severity: str
    safety: float | None = None
    determination: str = ""
    action: str = ""
    driver: str = ""
    confidence: float | None = None

    # The three numbers no tier may lose: liquidity, top-10, safety.
    liquidity_usd: float | None = None
    top10_pct: float | None = None
    volume_24h: float | None = None
    holders: int | None = None
    price_usd: float | None = None
    change_24h: float | None = None

    cohorts: tuple[Cohort, ...] = ()
    bundle_pct: float | None = None
    bundle_wallets: int | None = None
    dev_holdings_pct: float | None = None
    top10_addresses: tuple[tuple[str, float], ...] = ()

    lp_state: str = "unknown"
    lp_locked_pct: float | None = None
    impact_depth: dict[str, float | None] = field(default_factory=dict)

    mint_authority: str | None = None
    freeze_authority: str | None = None

    dev_tokens: int | None = None
    dev_rugs: int | None = None
    dev_scammer: bool | None = None

    kols: tuple[str, ...] = ()
    flags: tuple[tuple[str, str], ...] = ()
    flags_dropped: int = 0

    link_label: str = "Explorer"
    link_url: str = ""
    scanned_at: str = ""
    duration_ms: int | None = None
    source: str = "scanner"

    def with_flags(self, kept: tuple[tuple[str, str], ...], dropped: int) -> CardData:
        """Copy with a shorter flag list and the dropped count kept as data."""
        return replace(self, flags=kept, flags_dropped=dropped)

    def replace(self, **changes: object) -> CardData:
        """Shallow copy with field overrides — the prune pipeline's only mutator."""
        return replace(self, **changes)


@dataclass(frozen=True)
class RenderedCard:
    """A rendered card: HTML body, plain-text fallback, and the prune trail."""

    html: str
    text: str
    pruned: tuple[str, ...] = ()

    @property
    def chars(self) -> int:
        return len(self.html)

    @property
    def text_chars(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class ThumbSpec:
    """A rendered thumbnail on disk, sized for Telegram's photo slot."""

    path: str
    width: int
    height: int
    bytes_len: int
    severity: str
    symbol: str
