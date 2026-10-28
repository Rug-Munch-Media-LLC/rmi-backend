"""Lane delivery: flood control, retry/backoff, idempotency, failure isolation.

Four properties, each deliberate:

1. **Flood control** — one send per ``lane_min_interval_s`` inside a lane
   (Telegram allows roughly 20 messages/minute into one chat). The interval is
   a config value, not a constant every caller has to remember.
2. **Retry with backoff** — transient failures only (see :mod:`.retry`).
3. **Idempotency** — the ledger key ``(day, lane, slug)`` is written *after* a
   confirmed send, so a re-run skips what already went out.
4. **Failure isolation** — every lane and every post is wrapped: a dead lane
   (no chat id, revoked rights, a raising client) never blocks the others.

Logs are structured and always carry ``lane``/``slug``/``key``, plus
``message_id`` when the transport surfaces one.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.domains.telegram.editions.lanes import lane_chat_id, lane_live
from app.domains.telegram.editions.retry import DeliveryConfig, backoff_delay, is_retryable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from app.domains.telegram.editions.ledger import DeliveryLedger
    from app.domains.telegram.editions.payloads import TelegramPayload
    from app.domains.telegram.editions.routing import DeliveryPlan, PlannedPost
    from app.domains.telegram.editions.sender import Sender

logger = get_logger("telegram.editions.delivery")


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    lane: str
    slug: str
    ok: bool
    attempts: int
    message_id: int | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    results: tuple[DeliveryResult, ...] = ()
    skip_reasons: tuple[dict[str, str], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "failed": self.failed,
            "skipped": self.skipped,
            "ok": self.ok,
            "results": [
                {
                    "lane": row.lane,
                    "slug": row.slug,
                    "ok": row.ok,
                    "attempts": row.attempts,
                    "message_id": row.message_id,
                    "error": row.error,
                }
                for row in self.results
            ],
            "skip_reasons": list(self.skip_reasons),
        }


async def _attempt_send(
    post: PlannedPost,
    *,
    chat_id: int | str,
    sender: Sender,
    payload: TelegramPayload,
    config: DeliveryConfig,
    sleep: Callable[[float], Awaitable[None]],
) -> DeliveryResult:
    """Send one post with bounded retries. Never raises."""
    last_error = ""
    for attempt in range(1, config.max_attempts + 1):
        try:
            outcome = await sender.send(chat_id, payload, lane=post.lane)
            if outcome.ok:
                return DeliveryResult(post.lane, post.decision.slug, True, attempt, outcome.message_id)
            last_error = "transport returned not-ok"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if not is_retryable(exc):
                return DeliveryResult(post.lane, post.decision.slug, False, attempt, error=last_error)
        if attempt < config.max_attempts:
            await sleep(backoff_delay(attempt, config))
    return DeliveryResult(post.lane, post.decision.slug, False, config.max_attempts, error=last_error)


def _skip_all(
    lane: str, posts: Sequence[PlannedPost], reason: str, skips: list[dict[str, str]]
) -> None:
    skips.extend({"slug": post.decision.slug, "lane": lane, "reason": reason} for post in posts)


async def deliver(
    plan: DeliveryPlan,
    *,
    sender: Sender,
    ledger: DeliveryLedger,
    day: str,
    render: Callable[[PlannedPost], TelegramPayload],
    env: Mapping[str, str] | None = None,
    config: DeliveryConfig | None = None,
    use_gopass: bool = True,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> DeliveryReport:
    """Execute *plan* lane by lane. Returns a report; never raises."""
    cfg = config or DeliveryConfig()
    source = os.environ if env is None else env
    pause = sleep or asyncio.sleep
    results: list[DeliveryResult] = []
    skips: list[dict[str, str]] = [
        {"slug": row.slug, "lane": row.lane or "", "reason": row.reason} for row in plan.skipped
    ]
    sent = failed = 0
    skipped = len(plan.skipped)

    for lane in plan.lanes:
        posts = plan.for_lane(lane)
        try:
            chat_id = lane_chat_id(lane, env=source, use_gopass=use_gopass)
            if not lane_live(lane, source):
                skipped += len(posts)
                _skip_all(lane, posts, "lane_dry", skips)
                logger.info("tg_edition_lane_dry", lane=lane, posts=len(posts))
                continue
            if chat_id is None:
                skipped += len(posts)
                _skip_all(lane, posts, "no_chat_id", skips)
                logger.warning("tg_edition_lane_skipped", lane=lane, reason="no_chat_id")
                continue

            for index, post in enumerate(posts):
                if index:
                    await pause(cfg.lane_min_interval_s)
                try:
                    payload = render(post)
                except Exception as exc:
                    failed += 1
                    results.append(DeliveryResult(lane, post.decision.slug, False, 0, error=f"render: {exc}"))
                    logger.error("tg_edition_render_failed", lane=lane, slug=post.decision.slug, err=str(exc))
                    continue
                result = await _attempt_send(
                    post, chat_id=chat_id, sender=sender, payload=payload, config=cfg, sleep=pause
                )
                results.append(result)
                if result.ok:
                    sent += 1
                    ledger.record(post.key, day=day, lane=lane, slug=post.decision.slug, message_id=result.message_id)
                    logger.info(
                        "tg_edition_sent",
                        lane=lane,
                        slug=post.decision.slug,
                        key=post.key,
                        message_id=result.message_id,
                        attempts=result.attempts,
                        chars=payload.chars,
                        media=payload.needs_media,
                    )
                else:
                    failed += 1
                    logger.warning(
                        "tg_edition_failed",
                        lane=lane,
                        slug=post.decision.slug,
                        key=post.key,
                        attempts=result.attempts,
                        err=result.error,
                    )
        except Exception as exc:
            failed += len(posts)
            logger.error("tg_edition_lane_failed", lane=lane, err=str(exc))

    return DeliveryReport(sent=sent, failed=failed, skipped=skipped, results=tuple(results), skip_reasons=tuple(skips))


__all__ = ["DeliveryReport", "DeliveryResult", "deliver"]
