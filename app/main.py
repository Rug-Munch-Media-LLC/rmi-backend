"""RMI backend entrypoint.

Delegates to app.factory.create_app. Keeps `uvicorn app.main:app` working
as the canonical launch path referenced by Makefile, AGENTS.md, and
CONTRIBUTING.md.

Phase 1 of AUDIT-2026-Q3.md, item P1.1.
"""

from app.factory import create_app

app = create_app()
