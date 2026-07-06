"""RMI Backend - root entry point.

Minimal - delegates everything to app.factory.create_app().

Per v4.0 §T01 + modern factory pattern:
- main.py is the entry point ONLY
- All composition lives in app/factory.py
- Goal: <30 lines, single import, single uvicorn call

Run: `python -u main.py` (CMD in Dockerfile)
"""
from __future__ import annotations

import os

from app.factory import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
    )
