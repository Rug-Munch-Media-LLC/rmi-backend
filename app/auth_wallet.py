"""auth_wallet.py - DEPRECATED shim. Use app.domains.auth.wallet.

Phase 4 of AUDIT-2026-Q3.md moved this to app/domains/auth/wallet/.
This shim re-exports the public surface for legacy callers.
"""

from __future__ import annotations

from app.domains.auth.wallet import *
