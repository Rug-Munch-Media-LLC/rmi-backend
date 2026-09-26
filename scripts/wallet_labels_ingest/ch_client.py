#!/usr/bin/env python3
"""Thin ClickHouse query shim covering both supported client APIs.

``clickhouse-connect`` exposes ``query``/``insert``; ``clickhouse_driver``
exposes ``execute``. The two shims below pick the right call so every source
module can stay client-agnostic.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["ch_execute", "ch_select"]


def _insert_via_http(ch: Any, query: str, params: list) -> Any:
    """clickhouse-connect INSERT — needs table name + column names."""
    m = re.search(r"INTO\s+(\S+)", query, re.IGNORECASE)
    table = m.group(1) if m else None
    if not table or not params:
        return None
    col_names = list(params[0].keys())
    row_data = [tuple(p.values()) for p in params]
    return ch.insert(table, row_data, column_names=col_names)


def ch_execute(ch: Any, query: str, params: list | None = None) -> Any:
    """Execute an INSERT/other statement against either client API."""
    if hasattr(ch, "query"):
        if query.strip().upper().startswith("INSERT"):
            return _insert_via_http(ch, query, params) if params else None
        return ch.query(query, parameters=params) if params else ch.query(query)
    return ch.execute(query, params or [])


def ch_select(ch: Any, query: str, params: dict | None = None) -> list[Any]:
    """Run a SELECT and return result rows (empty list when nothing came back)."""
    if hasattr(ch, "query"):
        result = ch.query(query, parameters=params) if params else ch.query(query)
        return result.result_rows if result else []
    return ch.execute(query, params) if params else ch.execute(query)
