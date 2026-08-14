"""
Admin Bot - Callback Query Handler
====================================
Inline keyboard callback routing for the admin bot.

The historical single-function ``handle_callback`` (1269 lines) was split
by callback-data prefix into ``_dispatch`` (owner gate + ordered routing)
plus per-prefix handler modules. The public entry point is unchanged:

    from app.domains.telegram.adminbot.callbacks import handle_callback
"""

from __future__ import annotations

from app.domains.telegram.adminbot.callbacks._dispatch import handle_callback

__all__ = ["handle_callback"]
