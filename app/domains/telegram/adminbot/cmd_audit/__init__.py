"""Command audit + /diag: one durable JSONL record per inbound command."""

from __future__ import annotations

from .install import install, known_commands
from .mirror import mirror_inbound
from .record import record_command
from .register import register

__all__ = ["install", "known_commands", "mirror_inbound", "record_command", "register"]
