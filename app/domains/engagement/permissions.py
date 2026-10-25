"""Actors, ownership checks and the comment status machine.

Moderation transitions live here so the existing admin/moderation path and the
engagement router share one definition of what a status change means.
"""

from __future__ import annotations

from dataclasses import dataclass

MODERATOR_ROLES: frozenset[str] = frozenset({"ADMIN", "MODERATOR"})

# ``removed`` is terminal: content stays for audit but never returns to public
# view. Only a moderator may drive any transition at all.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "visible": frozenset({"hidden", "removed"}),
    "hidden": frozenset({"visible", "removed"}),
    "removed": frozenset(),
}


@dataclass(frozen=True, slots=True)
class Actor:
    """Caller identity resolved from the shared JWT (no parallel user system)."""

    user_id: str
    role: str = "USER"

    @property
    def is_moderator(self) -> bool:
        return self.role.upper() in MODERATOR_ROLES


@dataclass(frozen=True, slots=True)
class CommentState:
    """The moderation-relevant slice of a comment row."""

    user_id: str
    status: str
    deleted: bool


def is_author(actor: Actor, owner_id: str) -> bool:
    return actor.user_id == owner_id


def can_edit(actor: Actor, state: CommentState) -> bool:
    """Authors may edit their own live, publicly visible comment."""
    return is_author(actor, state.user_id) and not state.deleted and state.status == "visible"


def can_delete(actor: Actor, state: CommentState) -> bool:
    """Author or moderator. Already-deleted rows are a no-op, not a 403."""
    if state.deleted:
        return False
    return is_author(actor, state.user_id) or actor.is_moderator


def can_moderate(actor: Actor) -> bool:
    return actor.is_moderator


def next_status(current: str, requested: str) -> str:
    """Validate a moderation transition. Raises ``ValueError`` when illegal."""
    if requested == current:
        return current
    if requested not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"illegal status transition {current!r} -> {requested!r}")
    return requested
