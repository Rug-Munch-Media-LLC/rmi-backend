"""
Market Intelligence Router - /api/v1/market/*
BACKWARD COMPATIBILITY SHIM — delegates to app.routers.market_intel package.
All code has been refactored into app/routers/market_intel/:
  - cache.py: In-memory cache layer
  - utils.py: Utility functions
  - providers.py: External API clients (CoinGecko, DexScreener, etc.)
  - routes/overview.py: Overview & sentiment endpoints
  - routes/market_data.py: Token & market data endpoints
  - routes/scams.py: Scam detection & alerts feed
  - routes/premium.py: Premium features
  - routes/hyperliquid.py: Hyperliquid endpoints
"""

from app.routers.market_intel import router

__all__ = ["router"]
