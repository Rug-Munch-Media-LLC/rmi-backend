"""
Telegram Admin Bot Domain Package

Exports:
- bot module (main entry point)
- admin_ai module (AI gateway)
- ai_session module (AI session & OC confirmation state)
- ai_keyboard module (AI keyboard builders)
- alerts_feed module (alert forwarding)
- other adminbot submodules
"""

from app.domains.telegram.adminbot.admin_ai import (
    DEFAULT_MODEL,
    MODEL_IDS,
    MODELS,
    OPENCODE_BIN,
    OPENCODE_TIMEOUT,
    READ_ONLY_AGENT,
    chat,
    run_opencode,
    run_quick_task,
)
from app.domains.telegram.adminbot.ai_keyboard import (
    list_models,
    model_keyboard,
)
from app.domains.telegram.adminbot.ai_session import (
    cancel_oc_prompt,
    clear_history,
    get_session,
    get_usage,
    pop_oc_prompt,
    set_model,
    stage_oc_prompt,
)
from app.domains.telegram.adminbot.alerts_feed import (
    start_alerts_feed,
)
from app.domains.telegram.adminbot.bot import (
    main,
)

__all__ = [
    "DEFAULT_MODEL",
    "MODELS",
    "MODEL_IDS",
    "OPENCODE_BIN",
    "OPENCODE_TIMEOUT",
    "READ_ONLY_AGENT",
    "admin_ai",
    "ai_keyboard",
    "ai_session",
    "alerts_feed",
    "bot",
    "cancel_oc_prompt",
    "chat",
    "clear_history",
    "get_session",
    "get_usage",
    "list_models",
    "main",
    "model_keyboard",
    "pop_oc_prompt",
    "run_opencode",
    "run_quick_task",
    "set_model",
    "stage_oc_prompt",
    "start_alerts_feed",
]
