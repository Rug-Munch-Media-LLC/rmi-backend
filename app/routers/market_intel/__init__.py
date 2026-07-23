"""
Market Intelligence Package
Re-exports the router from the routes package for backward compatibility.
"""

from app.routers.market_intel.routes import router

__all__ = ["router"]
