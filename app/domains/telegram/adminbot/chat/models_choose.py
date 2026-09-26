"""Record the owner's model choice."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .lanes import LANES
from .models_current import KEY

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = logging.getLogger("adminbot.chat.choose")


def choose(context: ContextTypes.DEFAULT_TYPE, lane_id: str) -> str:
    """Record the choice and return a human label.

    An unknown lane is rejected rather than stored, so a stale button cannot
    leave the bot pointed at something that no longer exists.
    """
    known = {lane["id"]: lane["label"] for lane in LANES}
    label = known.get(lane_id)
    if label is None:
        return "Unknown model"
    if context.user_data is None:
        # PTB types this as Optional even for a non-persistence context; the
        # dict is created on write, so refusing is not a lost choice.
        logger.warning("model_choice_dropped lane=%s reason=no_user_data", lane_id)
        return "Unknown model"
    context.user_data[KEY] = lane_id
    return label
