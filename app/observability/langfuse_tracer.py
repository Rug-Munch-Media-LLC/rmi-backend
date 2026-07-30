"""Langfuse LLM observability integration for rmi-backend agents."""

from __future__ import annotations

import os
from typing import Any

from langfuse import Langfuse

from app.core.logging import get_logger

logger = get_logger(__name__)

_langfuse_client = None


def get_langfuse() -> Langfuse:
    global _langfuse_client
    if _langfuse_client is None:
        _langfuse_client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-dev"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-dev"),
            host=os.getenv("LANGFUSE_HOST", "http://localhost:3004"),
        )
    return _langfuse_client


def trace_agent_call(agent_name: str, input_data: dict, output_data: dict, **kwargs) -> Any | None:
    """Trace an agent call in Langfuse."""
    try:
        lf = get_langfuse()
        trace = lf.trace(name=f"agent:{agent_name}", input=input_data, output=output_data, **kwargs)
        return trace
    except Exception as e:
        logger.warning("langfuse_trace_error agent=%s error=%s", agent_name, e)
        return None
