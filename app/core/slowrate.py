"""Shared slowapi rate limiter instance for auth endpoint brute-force protection.

Single source of truth for the Limiter. Imported by:
  - app.middleware_setup  → registers SlowAPIMiddleware + 429 handler
  - app.domains.auth      → @limiter.limit(...) decorators on sensitive endpoints

Uses client IP as the rate-limit key (per-IP bucket).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
