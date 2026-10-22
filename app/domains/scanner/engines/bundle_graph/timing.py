"""Slot-level clustering of launch buyers.

Two wallets are "co-timed" when their first buy lands inside the same short
run of slots. That is a *timing* observation only: it says the buys were
submitted together, not that the same person funded them. The graph keeps
the distinction so a single timing signal can never be reported as a bundle.
"""

from __future__ import annotations

from .models import WalletPosition

#: Default co-timing tolerance. Two Solana slots (~400ms each) is the widest
#: window a Jito bundle can plausibly span without the block landing between
#: them; anything looser starts catching ordinary early buyers.
DEFAULT_WINDOW_SLOTS = 2

#: A cluster has to be more than "two wallets" to be interesting.
MIN_CLUSTER_SIZE = 3


def same_slot_groups(positions: dict[str, WalletPosition]) -> list[list[str]]:
    """Wallets whose first buy landed in the identical slot."""
    buckets: dict[int, list[str]] = {}
    for addr, pos in positions.items():
        buckets.setdefault(pos.first_slot, []).append(addr)
    return [sorted(addrs) for addrs in buckets.values() if len(addrs) > 1]


def cluster_by_window(
    positions: dict[str, WalletPosition],
    window_slots: int = DEFAULT_WINDOW_SLOTS,
    min_size: int = MIN_CLUSTER_SIZE,
) -> list[list[str]]:
    """Group wallets whose first buys fall inside ``window_slots`` of each other.

    Anchored on the earliest slot, then extended greedily: the anchor is the
    first buy of the run, which is what an observer would see on chain.
    """
    if not positions:
        return []
    ordered = sorted(positions.values(), key=lambda p: p.first_slot)
    clusters: list[list[str]] = []
    current: list[str] = []
    anchor = ordered[0].first_slot
    for pos in ordered:
        if pos.first_slot - anchor <= window_slots:
            current.append(pos.address)
            continue
        if len(current) >= min_size:
            clusters.append(current)
        current = [pos.address]
        anchor = pos.first_slot
    if len(current) >= min_size:
        clusters.append(current)
    return clusters


def span_slots(positions: dict[str, WalletPosition], wallets: list[str]) -> int:
    """Slot distance between the first and last first-buy in a cluster."""
    slots = [positions[w].first_slot for w in wallets if w in positions]
    if not slots:
        return 0
    return max(slots) - min(slots)


def cluster_still_holding(positions: dict[str, WalletPosition], wallets: list[str]) -> bool:
    """True when more than half of a cluster still holds its tokens."""
    members = [positions[w] for w in wallets if w in positions]
    if not members:
        return False
    held = sum(1 for m in members if m.still_holding)
    return held * 2 > len(members)
