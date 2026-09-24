"""Owner gate for privileged actions."""

from __future__ import annotations

from app.domains.telegram.adminbot.config import OWNER_IDS


def is_owner(user_id: int | None) -> bool:
    """True when the sender is the owner.

    Owner resolution already has a three-source fallback (env, owner env var,
    secret store). Everything privileged funnels through this check so there is
    exactly one place that decides who may act.
    """
    return bool(user_id) and int(user_id) in OWNER_IDS
