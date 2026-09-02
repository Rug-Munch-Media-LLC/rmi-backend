"""RMI Daily Pipeline — content/alerts orchestrator + scheduled CLI entrypoint.

Daily run (methodology ``2026-08-13-morning-brief-methodology.md`` §2 / §6):

    `python -m app.domains.content.daily_pipeline --run`  (05:30 UTC)

Pipeline order — every step fails open (try/except per step, never raises):

    1. ``news``     — ``sweep_channels`` over the *enabled news-kind* Telegram
                      channels from the tg registry (deduped ``NewsArticle``).
    2. ``alerts``   — ``collect_pipeline_alerts`` over injected detector
                      outputs (``source_outputs``, empty by default) → the
                      per-channel routing plan. The transport ``send`` is
                      embedded in the plan but never invoked by this module.
    3. ``shill``    — ``run_shill_detection`` over the swept posts → syndicate
                      ``PlatformAlert`` list.
    4. ``briefing`` — the §3/§6 assembler (``gather_briefing_context`` →
                      ``draft_briefing`` → ``review_briefing``). Publishes the
                      Ghost ``briefing`` post only when ``dry_run=False``.

``run_daily_pipeline`` returns ``{step: {ran, count, error}}`` for every step
(``ran=False`` = skipped via ``--step``). ``dry_run=True`` is the default and
the systemd unit default: it gathers real data but skips publish/send so the
owner can veto/edit before the live push (methodology §2).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.databus.tg_channel_registry import enabled_channels, to_news_source
from app.domains.alerts.adapters import collect_pipeline_alerts
from app.domains.content.briefing import (
    draft_briefing,
    gather_briefing_context,
    publish_briefing,
    publish_briefing_telegram,
    review_briefing,
)
from app.domains.news_ingest.ingest import sweep_channels
from app.domains.shill_alert.models import ShillPost
from app.domains.shill_alert.service import run_shill_detection

if TYPE_CHECKING:
    from app.domains.alerts.models import PlatformAlert
    from app.domains.news_ingest.models import NewsArticle

logger = get_logger(__name__)

# Pipeline steps in execution order (§6).
STEPS = ("news", "alerts", "shill", "briefing")

DEFAULT_SWEEP_LIMIT = 50


# ── Small helpers ────────────────────────────────────────────────────────────


def _step_result(ran: bool, count: int, error: str | None) -> dict[str, Any]:
    """One entry of the summary contract ``{ran, count, error}``."""
    return {"ran": ran, "count": count, "error": error}


def _error_text(exc: BaseException) -> str:
    """Compact, bounded exception text for the summary (never leaks traceback)."""
    return f"{type(exc).__name__}: {exc}"[:300]


def news_channels_from_registry() -> list[dict[str, Any]]:
    """Enabled news-kind channels from the tg registry, mapped to source shape.

    Only entries with ``kind == "news"`` and ``enabled is True`` participate.
    Malformed entries are skipped (``to_news_source`` raises ``ValueError``).
    Pure: no network, no DB.
    """
    channels: list[dict[str, Any]] = []
    for entry in enabled_channels():
        if entry.get("kind") != "news":
            continue
        try:
            channels.append(to_news_source(entry))
        except ValueError:
            logger.debug("daily_pipeline_skip_channel entry=%r", entry.get("username"))
            continue
    return channels


async def _build_telegram_client() -> Any:
    """Construct + start the headless Telegram client, or ``None`` on failure.

    Fail-open: when credentials/network are unavailable (e.g. inside the
    systemd unit without gopass) the pipeline degrades to an empty news sweep
    rather than aborting the whole run.
    """
    try:
        from app.domains.telegram.headless_client import HeadlessTelegramClient

        client = HeadlessTelegramClient()
        await client.start()
        return client
    except Exception as e:  # fail-open — news sweep is optional
        logger.warning("daily_pipeline_client_unavailable err=%s", str(e)[:160])
        return None


# ── Step functions (injectable; monkeypatched in tests) ─────────────────────


async def run_news_step(*, client: Any | None = None, limit: int = DEFAULT_SWEEP_LIMIT) -> list[NewsArticle]:
    """Sweep enabled news channels → deduped ``NewsArticle`` list (fail-open)."""
    channels = news_channels_from_registry()
    if client is None:
        client = await _build_telegram_client()
    return await sweep_channels(client, channels, limit=limit)


async def run_alerts_step(
    source_outputs: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Adapt/dedupe/route injected detector outputs → routing plan (no sends)."""
    return collect_pipeline_alerts(source_outputs or {})


def articles_to_shill_posts(articles: list[NewsArticle]) -> list[ShillPost]:
    """Map deduped ``NewsArticle`` objects to ``ShillPost`` for shill detection.

    Author = the source channel (the identity that posted). Pure mapping.
    """
    posts: list[ShillPost] = []
    for article in articles or []:
        ts = 0.0
        if article.posted_at is not None:
            posted = (
                article.posted_at if article.posted_at.tzinfo is not None else article.posted_at.replace(tzinfo=UTC)
            )
            ts = posted.timestamp()
        posts.append(
            ShillPost(
                author=article.source_channel or "unknown",
                text=article.body or article.title or "",
                ts=ts,
                source="telegram",
                channel=article.source_channel or None,
                url=article.source_url or None,
            )
        )
    return posts


async def run_shill_step(articles: list[NewsArticle]) -> list[PlatformAlert]:
    """Run coordinated-shill detection over the swept news posts."""
    alerts = run_shill_detection(articles_to_shill_posts(articles))
    # Vectorize each coordinated-shill alert into Qdrant social_intel
    # (fire-and-forget — never blocks the pipeline).
    try:
        from app.rag.scan_intel import embed_shill_detection_bg

        for alert in alerts:
            d = alert.to_dict()
            embed_shill_detection_bg(
                {
                    "handle": d.get("author") or d.get("source") or "unknown",
                    "token": d.get("token_address") or d.get("token") or "unknown",
                    "platform": d.get("source") or "telegram",
                    "suspicion_score": d.get("confidence"),
                    "wallet": d.get("wallet") or "",
                    "description": d.get("description") or d.get("title") or "",
                }
            )
    except Exception as exc:  # embedding is strictly best-effort
        logger.debug("shill_embed_schedule_failed err=%s", str(exc)[:120])
    return alerts


async def run_briefing_step(
    *,
    dry_run: bool = True,
    now: datetime | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Run the §6 assembler: gather → draft → review → publish.

    ``dry_run=True`` (default) gathers, drafts and reviews but skips both
    publishers (owner veto window, methodology §2). When live, the Ghost
    ``briefing`` post is published first (``publish_briefing`` →
    ``content_platform.create_post``), then a compact version is posted to
    the news/main Telegram channel (``publish_briefing_telegram`` — skipped +
    logged when no channel id is configured). Both publishers fail open:
    a raising/broken publisher is recorded in ``publish_error`` /
    ``telegram_error`` and never crashes the run.

    Returns ``{"draft", "review", "published", "telegram"}`` plus optional
    ``publish_error`` / ``telegram_error`` strings.
    """
    context = await gather_briefing_context()
    if now is not None:
        context["generated_at"] = now.isoformat(timespec="seconds")
    draft = await draft_briefing(context)
    review = review_briefing(draft, context)
    result: dict[str, Any] = {
        "draft": draft,
        "review": review,
        "published": None,
        "telegram": None,
    }
    if dry_run:
        return result

    try:
        result["published"] = await publish_briefing(draft)
    except Exception as e:  # fail-open: a broken Ghost publisher never crashes the run
        logger.warning("daily_pipeline_briefing_publish_failed err=%s", _error_text(e))
        result["publish_error"] = _error_text(e)
    try:
        result["telegram"] = await publish_briefing_telegram(draft, client=client)
    except Exception as e:  # fail-open: a broken Telegram publisher never crashes the run
        logger.warning("daily_pipeline_briefing_telegram_failed err=%s", _error_text(e))
        result["telegram_error"] = _error_text(e)
    return result


# ── Orchestrator ─────────────────────────────────────────────────────────────


async def run_daily_pipeline(
    *,
    now: datetime | None = None,
    dry_run: bool = True,
    steps: set[str] | None = None,
    source_outputs: dict[str, list[dict[str, Any]]] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Run the daily pipeline in §6 order. Never raises.

    Args:
        now: Timestamp for the summary (deterministic runs/tests). Defaults to
            the current UTC time.
        dry_run: When True (default) the briefing is drafted + reviewed but
            NOT published; alerts are routed into a plan but never sent.
        steps: Subset of ``STEPS`` to run; ``None`` runs all. Skipped steps are
            reported with ``ran=False``.
        source_outputs: Detector raw outputs for the alerts step
            (``{"source": [raw, ...]}``). Defaults to ``{}``.
        client: Optional Telegram client for the news sweep. When omitted the
            headless client is constructed lazily (fail-open → empty sweep).

    Returns:
        ``{"ran_at", "dry_run", <step>: {"ran", "count", "error"}, ...}`` plus
        ``briefing_draft`` (``{draft, review, published}``) when the briefing
        step ran.
    """
    selected = steps if steps is not None else set(STEPS)
    selected = {s for s in selected if s in STEPS}

    summary: dict[str, Any] = {
        "ran_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "dry_run": dry_run,
    }

    articles: list[NewsArticle] = []

    # 1. news sweep (enabled news channels → deduped articles)
    summary["news"] = _step_result(False, 0, None)
    if "news" in selected:
        try:
            articles = await run_news_step(client=client)
            summary["news"] = _step_result(True, len(articles), None)
        except Exception as e:
            logger.exception("daily_pipeline_step_failed step=news")
            summary["news"] = _step_result(True, 0, _error_text(e))

    # 2. alerts collect (injected detector outputs → routing plan, no send)
    summary["alerts"] = _step_result(False, 0, None)
    if "alerts" in selected:
        try:
            plan = await run_alerts_step(source_outputs=source_outputs)
            summary["alerts"] = _step_result(True, len(plan), None)
        except Exception as e:
            logger.exception("daily_pipeline_step_failed step=alerts")
            summary["alerts"] = _step_result(True, 0, _error_text(e))

    # 3. shill detection (over the swept posts)
    summary["shill"] = _step_result(False, 0, None)
    if "shill" in selected:
        try:
            shill_alerts = await run_shill_step(articles)
            summary["shill"] = _step_result(True, len(shill_alerts), None)
        except Exception as e:
            logger.exception("daily_pipeline_step_failed step=shill")
            summary["shill"] = _step_result(True, 0, _error_text(e))

    # 4. briefing (gather → draft → review → publish unless dry-run)
    summary["briefing"] = _step_result(False, 0, None)
    if "briefing" in selected:
        try:
            result = await run_briefing_step(dry_run=dry_run, now=now)
            draft_text = str(result.get("draft") or "")
            summary["briefing"] = _step_result(True, 1 if draft_text.strip() else 0, None)
            summary["briefing_draft"] = {
                "draft": result.get("draft"),
                "review": result.get("review"),
                "published": result.get("published"),
                "telegram": result.get("telegram"),
                "publish_error": result.get("publish_error"),
                "telegram_error": result.get("telegram_error"),
            }
        except Exception as e:
            logger.exception("daily_pipeline_step_failed step=briefing")
            summary["briefing"] = _step_result(True, 0, _error_text(e))

    return summary


# ── CLI entrypoint ───────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for ``python -m app.domains.content.daily_pipeline``."""
    parser = argparse.ArgumentParser(
        prog="python -m app.domains.content.daily_pipeline",
        description="RMI Daily Briefing — news sweep → alerts → shill → briefing (05:30 UTC).",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run the daily pipeline (default: print this help and exit).",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Publish/send for real (dry-run is the default — owner veto window).",
    )
    parser.add_argument(
        "--step",
        choices=list(STEPS),
        action="append",
        default=None,
        help="Run only the given step(s); repeatable. Choices: %(choices)s.",
    )
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    """Run the pipeline and print the JSON summary. Exit 1 when a step errored."""
    dry_run = not args.no_dry_run
    steps = set(args.step) if args.step else None
    summary = await run_daily_pipeline(dry_run=dry_run, steps=steps)
    print(json.dumps(summary, indent=2, default=str))
    errored = any(isinstance(entry, dict) and entry.get("error") for entry in summary.values())
    return 1 if errored else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint (returns process exit code)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.run:
        parser.print_help()
        return 0
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
