"""RMI Admin Bot — Fleet ChatOps for the Rug Munch intelligence stack.

Exposes the ``main()`` entry point that builds and runs the Telegram
Application with all admin handlers plus the embedded Redis alert consumer.
"""

from __future__ import annotations
from app.domains.telegram.adminbot.bot import main  # noqa: F401
