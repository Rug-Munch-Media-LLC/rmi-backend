"""Unit tests for the personal channel (channels/personal_channel.py).

Covers: reranker-gate selection (threshold + fallback), operator-voice
caption building (real-content only, deterministic), Ghost Admin API JWT
signing, and chat-id resolution order. No live network, no live gopass.
"""

from __future__ import annotations

import base64
import json
import sys
import types
from typing import TYPE_CHECKING

# Hermetic import guard: the rugmunchbot package ``__init__`` eagerly imports
# ``bot`` → ``alert_worker`` → ``config``, which is often mid-refactor in the
# working tree (registry field renames). These tests don't exercise the bot,
# so stub ``bot.main`` before the app imports run. In a consistent tree this
# is a no-op that keeps the test suite independent of unrelated modules.
_bot_stub = types.ModuleType("app.domains.telegram.rugmunchbot.bot")
_bot_stub.main = lambda: None  # type: ignore[attr-defined]
sys.modules.setdefault("app.domains.telegram.rugmunchbot.bot", _bot_stub)

from app.domains.telegram.rugmunchbot.channels import personal_channel as pc  # noqa: E402
from app.domains.telegram.rugmunchbot.channels.importance import ScoredArticle  # noqa: E402

if TYPE_CHECKING:
    from app.domains.news_ingest.models import NewsArticle


def _article(
    title: str = "ETF approval sends Bitcoin to new all-time high",
    body: str = "The SEC approved the spot ETF. Markets reacted instantly.",
) -> NewsArticle:
    from app.domains.news_ingest.models import NewsArticle

    return NewsArticle(
        title=title,
        body=body,
        source_channel="cointelegraph",
        source_url="https://t.me/cointelegraph/123",
    )


def _scored(importance: float, *, fallback: bool = False) -> ScoredArticle:
    return ScoredArticle(article=_article(), importance=importance, fallback=fallback)


class TestSelectPersonal:
    def test_rerank_mode_keeps_only_above_threshold(self) -> None:
        scored = [_scored(0.99), _scored(0.71), _scored(0.69), _scored(0.4)]
        selected = pc.select_personal(scored, top_n=10)
        assert [s.importance for s in selected] == [0.99, 0.71]

    def test_default_threshold_is_stricter_than_news(self) -> None:
        assert pc.PERSONAL_CHANNEL_THRESHOLD >= 0.7
        assert pc.PERSONAL_CHANNEL_THRESHOLD > pc.IMPORTANCE_THRESHOLD

    def test_fallback_mode_keeps_top_n(self) -> None:
        scored = [_scored(0.5, fallback=True), _scored(0.3, fallback=True), _scored(0.9, fallback=True)]
        selected = pc.select_personal(scored, top_n=2)
        assert [s.importance for s in selected] == [0.9, 0.5]

    def test_empty_input(self) -> None:
        assert pc.select_personal([]) == []

    def test_top_n_capped(self) -> None:
        scored = [_scored(0.9), _scored(0.8), _scored(0.75), _scored(0.71)]
        assert len(pc.select_personal(scored, top_n=2)) == 2


class TestBuildCaption:
    def test_caption_uses_real_article_content_only(self) -> None:
        article = _article()
        caption = pc.build_caption(article)
        assert article.title in caption
        assert article.source_channel in caption
        assert article.source_url in caption
        # lead is a real excerpt of the body, not invented
        assert "SEC approved" in caption

    def test_caption_is_deterministic_per_url(self) -> None:
        article = _article()
        assert pc.build_caption(article) == pc.build_caption(article)

    def test_template_rotation_stays_in_bounds(self) -> None:
        for i in range(8):
            caption = pc.build_caption(_article(title=f"Headline {i}"), template_idx=i)
            assert caption  # every template renders non-empty

    def test_lead_clips_long_body_at_sentence_boundary(self) -> None:
        article = _article(body="First sentence with enough words to matter. Second sentence is filler here.")
        lead = pc._lead(article)
        assert lead.startswith("First sentence")
        assert len(lead) <= pc._LEAD_CHARS


class TestGhostAdminJwt:
    def test_jwt_shape_and_kid(self) -> None:
        token = pc.ghost_admin_jwt("deadbeef01:supersecret")
        parts = token.split(".")
        assert len(parts) == 3
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
        assert header["kid"] == "deadbeef01"
        assert header["alg"] == "HS256"

    def test_jwt_aud_is_admin(self) -> None:
        token = pc.ghost_admin_jwt("id:secret")
        payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        assert payload["aud"] == "/admin/"
        assert payload["exp"] > payload["iat"]

    def test_malformed_key_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="<id>:<secret>"):
            pc.ghost_admin_jwt("no-separator")


class TestResolveChatId:
    def test_dedicated_env_wins(self, monkeypatch) -> None:  # pytest fixture
        monkeypatch.setenv("RMI_PERSONAL_CHANNEL_ID", "-1000000000001")
        assert pc.resolve_personal_chat_id() == "-1000000000001"

    def test_empty_env_falls_back_to_registry_main(self, monkeypatch) -> None:
        monkeypatch.delenv("RMI_PERSONAL_CHANNEL_ID", raising=False)
        # "main" role has a documented fallback, so resolution must succeed.
        resolved = pc.resolve_personal_chat_id()
        assert resolved is not None
        assert resolved.startswith("-100")


class TestGhostPublishFailOpen:
    async def test_missing_key_returns_error_not_raise(self) -> None:
        article = _article()
        result = await pc.ghost_publish_article(article, "caption", api_key="")
        assert result["published"] is False
        assert "not set" in result["error"]
