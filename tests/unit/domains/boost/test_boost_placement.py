"""Placement isolation: paid boosts never leak into ranking or external feeds.

Structural assertions on the surfaces we must not touch. Cheap, and they catch
the one regression that matters commercially: a paid placement silently
altering trending rank.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_trending_rank_never_reads_boost_inventory() -> None:
    source = _read("app/routers/v1/market.py")
    assert "boost_purchases" not in source
    assert "BoostPurchase" not in source
    assert "domains.boost" not in source


def test_launches_external_promo_is_not_our_inventory() -> None:
    """DexScreener promo stays discovery-labelled, never conflated with ours."""
    source = _read("app/routers/v1/launches.py")
    assert 'source": "dexscreener_boost"' not in source
    assert 'source": "dexscreener_external_promo"' in source
    assert "domains.boost" not in source


def test_placement_module_is_read_only() -> None:
    listing = _read("app/domains/boost/listing.py")
    for forbidden in ("session.add", "insert(", "update(", "delete("):
        assert forbidden not in listing
