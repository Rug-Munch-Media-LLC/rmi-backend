"""auth.py - DEPRECATED shim. Use app.domains.auth.

Phase 4 of AUDIT-2026-Q3.md moved this to app/domains/auth/.
This shim re-exports the public surface for legacy callers.
"""

from __future__ import annotations

from app.domains.auth import *
