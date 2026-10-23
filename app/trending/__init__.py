"""Trending-token feed assembled from our own indexed tables.

Sources (all fail-open):
  wallet_memory.dex_events        trade activity, volume, price series
  wallet_memory.token_meta_ref    symbol / name / decimals
  wallet_memory.pumpfun_launches  Solana launchpad metadata + bonding curve
  wallet_memory.entity_labels     known-entity veto (exchange / bridge / scam)
  optional B2 Parquet via DuckDB  when TRENDING_PARQUET_GLOB is set
"""

from app.trending.service import build_trending

__all__ = ["build_trending"]
