"""Fail-closed provider selection for the free chat pool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.assistant_gateway.free_pool.spec import PoolSpec, ProviderSpec
    from app.assistant_gateway.free_pool.state import PoolState


class FreePoolExhausted(RuntimeError):
    """Every free lane is down or out of quota — never degrade to paid/local."""

    def __init__(self, code: str, reason: str, snapshot: dict[str, Any]) -> None:
        self.code = code
        self.reason = reason
        self.snapshot = snapshot
        super().__init__(f"{code}: {reason}")

    def as_error(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "reason": self.reason,
            "providers": self.snapshot,
            "fallback": None,
        }


def pick_model(spec: PoolSpec, state: PoolState) -> ProviderSpec:
    """First healthy provider in fallback order, else :class:`FreePoolExhausted`."""
    candidates = state.candidates()
    if candidates:
        return candidates[0]  # type: ignore[return-value]
    snapshot = state.snapshot()
    open_breakers = [pid for pid, row in snapshot.items() if row["breaker"] == "open"]
    reason = (
        "all free lanes open-circuit: " + ", ".join(sorted(open_breakers))
        if open_breakers
        else "all free lanes past their request quota"
    )
    if not any(row["enabled"] for row in snapshot.values()):
        reason = "no free lane is enabled"
    raise FreePoolExhausted(spec.fail_closed_code, reason, snapshot)
