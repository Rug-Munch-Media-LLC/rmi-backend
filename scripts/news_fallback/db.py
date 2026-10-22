"""Single connection helper: env-driven, no credentials in source."""

from __future__ import annotations

import os

import psycopg


def connect() -> psycopg.Connection:
    """Open a psycopg connection from NF_PG_* env vars."""
    return psycopg.connect(
        host=os.environ.get("NF_PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("NF_PG_PORT", "5432")),
        dbname=os.environ.get("NF_PG_DB", "rmi"),
        user=os.environ.get("NF_PG_USER", "rmi"),
        password=os.environ.get("NF_PG_PASSWORD", ""),
    )
