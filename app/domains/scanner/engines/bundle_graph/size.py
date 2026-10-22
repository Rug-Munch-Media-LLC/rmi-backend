"""Size metrics: how much of the supply and of the early flow a cluster owns.

Three denominators, each with its own strength:

* ``bundle_pct_of_supply`` -- cluster net tokens / proven supply. Strong, but
  only when a supply value could actually be derived; otherwise ``None``.
* ``bundle_pct_of_first_minute_volume`` -- cluster notional / first-minute
  notional. Exact, always available once trades exist.
* ``bundle_pct_of_early_tokens_bought`` -- cluster tokens / all tokens bought
  in the window. Exact and supply-independent; this is the honest fallback
  when supply cannot be proven.
"""

from __future__ import annotations

from .models import WalletPosition


def cluster_net_tokens(positions: dict[str, WalletPosition], wallets: list[str]) -> int:
    """Sum of the cluster's still-held tokens (raw units)."""
    return sum(positions[w].net_tokens_raw for w in wallets if w in positions)


def cluster_usd_spent(positions: dict[str, WalletPosition], wallets: list[str]) -> float:
    """Sum of the cluster's launch-window USD spend."""
    return sum(positions[w].usd_spent for w in wallets if w in positions)


def cluster_realised_usd(positions: dict[str, WalletPosition], wallets: list[str]) -> float:
    """Sum of the cluster's realised USD from sells."""
    return sum(positions[w].realised_usd for w in wallets if w in positions)


def pct(numerator: float | int | None, denominator: float | int | None) -> float | None:
    """Percentage of ``denominator``, ``None`` when the denominator is unusable."""
    if not denominator or denominator <= 0 or numerator is None:
        return None
    return round(float(numerator) * 100.0 / float(denominator), 2)


def supply_pct(net_tokens: int, supply_raw: int | None) -> float | None:
    """Cluster share of proven supply."""
    return pct(net_tokens, supply_raw)


def realised_sol(realised_usd: float, sol_price_usd: float | None) -> float | None:
    """Convert realised USD to SOL at the supplied price (``None`` if unknown)."""
    if not sol_price_usd:
        return None
    return round(realised_usd / sol_price_usd, 4)


def similar_size(positions: dict[str, WalletPosition], wallets: list[str]) -> bool:
    """True when members spent near-identical amounts (bot-template sizing).

    Coefficient of variation under 0.35 across at least three wallets. The
    check needs real spread: two equal buys prove nothing.
    """
    spends = [
        positions[w].usd_spent for w in wallets if w in positions and positions[w].usd_spent > 0
    ]
    if len(spends) < 3:
        return False
    mean = sum(spends) / len(spends)
    if mean <= 0:
        return False
    variance = sum((s - mean) ** 2 for s in spends) / len(spends)
    return (variance**0.5) / mean < 0.35
