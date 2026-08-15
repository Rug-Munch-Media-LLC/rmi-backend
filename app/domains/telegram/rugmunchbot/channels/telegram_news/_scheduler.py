"""Scheduled TG-news broadcast job (``post_tg_news_update``).

Submodule of ``channels/telegram_news.py`` — owns the **scheduled-broadcast**
logic: sweep → persist → importance-score → route digests to the news/alpha
channels, plus the additive feed/market-awareness side-effects.

Pipeline-order and patch-point contract: every stage that tests patch on the
shim (``sweep_telegram_news``, ``persist_articles``, ``score_importance``,
``inject_into_news_cache``, ``publish_market_awareness``) is resolved through
``channels/telegram_news.py`` at call time; the scheduler helpers
(``_chat_id`` / ``_remember`` / ``_dedupe_key`` / ``_safe_send``) are
re-imported lazily exactly like the original module so
``monkeypatch.setattr(channels_scheduler, "_chat_id", ...)`` keeps working.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from app.domains.telegram.rugmunchbot.channels.formatters import format_breaking_post, format_news_post
from app.domains.telegram.rugmunchbot.channels.importance import select_important
from app.domains.telegram.rugmunchbot.channels.telegram_news._digest import (
    _article_to_post_dict,
    _category_is_news,
)
from app.domains.telegram.rugmunchbot.channels.telegram_news._sources import TG_NEWS_POST_LIMIT
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

    from app.domains.telegram.rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

logger = get_logger("rmi_bot.channels.tg_news")


async def post_tg_news_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Sweep TG channels → persist to website → score importance → route digests.

    Returns 1 when a digest was posted (news or alpha), 0 otherwise. Fails
    open at every step: missing chat ids, no client, empty sweep, Postgres
    outage, or a Telegram send error all log and return without raising.

    Pipeline order (efficiency contract):
    ``read (bounded) → normalize → dedupe (multi-key: canonical URL / t.me
    URL / normalized title / content hash, in-batch + Redis cross-run) →
    store (news_items + Redis dedup mark) → reranker importance → route``.

    Routing: **news**-categorized articles are persisted and posted to the
    news channel; **alpha**-categorized articles are persisted and posted to
    the alpha channel (when ``_chat_id("alpha")`` resolves). Every swept
    article is persisted (store everything); only the articles
    ``select_important`` keeps (rerank importance >=
    ``RMI_NEWS_IMPORTANCE_THRESHOLD``, or the top-N under the heuristic
    fallback when Infinity /rerank is down) are posted — per category.

    Post variety on the news channel: when the top selected story carries a
    breaking marker (🚨/BREAKING/URGENT/JUST IN/FLASH in its title,
    ``is_breaking_article``) it is posted **solo immediately** with its
    official link (``format_breaking_post``); otherwise the top 3-5 stories
    form a numbered digest (``format_news_post``). The selection is sorted
    by rerank importance, secondarily by the deterministic
    ``lightweight_importance`` score.

    Additive side-effects (all fail-open):
    * freshly-persisted TG items are merged into the website full-feed Redis
      cache (``inject_into_news_cache``) so ``/api/v1/news/feed`` +
      ``/headlines`` surface them with attribution;
    * a lightweight market-awareness snapshot (top headlines + trending
      tokens) is published to ``rmi:news:market_awareness`` for the bot's
      chat context (``publish_market_awareness``);
    * the swept articles are clustered into stories and embedded into the
      RAG ``news_v2`` collection (``ingest_into_rag``) so RAG "benefits
      from" the news lane.
    """
    # Lazy import keeps the scheduler ↔ this module import graph acyclic
    # (scheduler.py imports this module at module scope for registration).
    from app.domains.telegram.rugmunchbot.channels.scheduler import (
        _chat_id,
        _dedupe_key,
        _remember,
        _safe_send,
    )

    news_chat_id = _chat_id("news")
    alpha_chat_id = _chat_id("alpha")
    if not news_chat_id and not alpha_chat_id:
        return 0

    articles = await _tg.sweep_telegram_news()
    if not articles:
        logger.debug("tg_news:no_fresh_articles")
        return 0

    counts = await _tg.persist_articles(articles)
    logger.info(
        "tg_news:sweep_done articles=%s ingested=%s duplicate=%s errors=%s",
        len(articles),
        counts["ingested"],
        counts["duplicate"],
        counts["errors"],
    )

    scored = await _tg.score_importance(articles)  # type: ignore[attr-defined]
    if not scored:
        logger.info("tg_news:importance_scoring_empty articles=%s", len(articles))
        return 0

    posted = 0

    # Route: news category → news channel
    if news_chat_id:
        news_scored = [s for s in scored if _category_is_news(s.article.category)]
        selected = select_important(news_scored, top_n=TG_NEWS_POST_LIMIT)
        # Secondary sort by the deterministic lightweight importance score
        # (stable) so the digest is ordered best-first even when the rerank
        # scores tie (e.g. heuristic fallback or stub tests).
        selected = sorted(selected, key=lambda s: s.importance, reverse=True)

        # Post variety: a top story carrying a breaking marker (🚨/BREAKING/
        # URGENT/JUST IN/FLASH in its title) is posted SOLO immediately with
        # its official link; otherwise the top 3-5 form a numbered digest.
        if selected and _tg.is_breaking_article(selected[0].article):
            top = selected[0].article
            post = _article_to_post_dict(top)
            if _remember("tgnews", _dedupe_key("tgnews", post)):
                ok = await _safe_send(ctx.bot, news_chat_id, format_breaking_post(post))
                if ok:
                    logger.info("tg_news:posted_breaking title=%s", top.title[:80])
                    posted += 1
        else:
            fresh = [
                scored_article.article
                for scored_article in selected
                if _remember("tgnews", _dedupe_key("tgnews", _article_to_post_dict(scored_article.article)))
            ]
            if fresh:
                ok = await _safe_send(
                    ctx.bot,
                    news_chat_id,
                    format_news_post([_article_to_post_dict(a) for a in fresh[:TG_NEWS_POST_LIMIT]]),
                )
                if ok:
                    logger.info("tg_news:posted_news count=%s", min(len(fresh), TG_NEWS_POST_LIMIT))
                    posted += 1

    # Route: alpha category → alpha channel (additive; no alpha chat id → skip)
    if alpha_chat_id:
        alpha_scored = [s for s in scored if s.article.category == "alpha"]
        selected_alpha = select_important(alpha_scored, top_n=TG_NEWS_POST_LIMIT)
        fresh_alpha = [
            scored_article.article
            for scored_article in selected_alpha
            if _remember("tgalpha", _dedupe_key("tgalpha", _article_to_post_dict(scored_article.article)))
        ]
        if fresh_alpha:
            ok = await _safe_send(
                ctx.bot,
                alpha_chat_id,
                format_news_post([_article_to_post_dict(a) for a in fresh_alpha[:TG_NEWS_POST_LIMIT]]),
            )
            if ok:
                logger.info("tg_news:posted_alpha count=%s", min(len(fresh_alpha), TG_NEWS_POST_LIMIT))
                posted += 1

    if not posted:
        logger.debug("tg_news:all_already_posted")

    # Additive, fail-open side-effects.
    await _tg.inject_into_news_cache(articles)
    await _tg.publish_market_awareness(articles, scored)
    # Feed RAG: cluster the swept articles into stories and embed into
    # ``news_v2`` (the collection the get_news RAG tool searches) so RAG
    # "benefits from" the news lane. Fail-open — a RAG outage never blocks
    # the website/Telegram halves.
    rag_counts = await _tg.ingest_into_rag(articles)
    if rag_counts.get("ingested") or rag_counts.get("errors"):
        logger.info(
            "tg_news:rag_side_effect stories=%s ingested=%s duplicates=%s errors=%s",
            rag_counts.get("stories", 0),
            rag_counts.get("ingested", 0),
            rag_counts.get("duplicates", 0),
            rag_counts.get("errors", 0),
        )

    return 1 if posted else 0
