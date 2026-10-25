"""PostgreSQL DSN composition for probes and config validation.

The docker/dev defaults used to carry a literal redacted password
(``postgresql://rmi:***@rmi-postgres:5432/rmi``), so every probe built from them
failed with "password authentication failed" while the app itself connected
fine through the ``POSTGRES_*`` variables. One canonical builder, from env.
"""

from __future__ import annotations

import os
from urllib.parse import quote


def postgres_dsn(host: str, default_db: str = "rmi") -> str:
    """Build a DSN from the same ``POSTGRES_*`` vars the application uses."""
    user = os.getenv("POSTGRES_USER", "rmi")
    password = os.getenv("POSTGRES_PASSWORD", "")
    database = os.getenv("POSTGRES_DB", default_db)
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{quote(password, safe='')}@{host}:{port}/{database}"
