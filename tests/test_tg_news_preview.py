"""Offline tests for the Telegram public-preview ingester (no network)."""

from __future__ import annotations

import json

from app.databus.tg_news import classify, databus, parse, store

PAGE = """
<div class="tgme_channel_info">
  <div class="tgme_channel_info_header"><div class="tgme_channel_info_header_title">
    <span dir="auto">Crypto News</span></div></div>
  <div class="tgme_channel_info_counters"><div class="tgme_channel_info_counter">
    <span class="counter_value">99.8K</span> <span class="counter_type">subscribers</span></div></div>
  <div class="tgme_channel_info_description">Crypto News - latest buzz</div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message" data-post="news_crypto/2">
    <div class="tgme_widget_message_text js-message_text">Bitcoin ETF inflows hit a record high today
      <a href="https://example.com/etf">source</a></div>
    <div class="tgme_widget_message_footer">
      <span class="tgme_widget_message_views">6.44K</span>
      <a class="tgme_widget_message_date" href="https://t.me/news_crypto/2">
        <time datetime="2026-10-21T20:13:03+00:00"></time></a>
    </div>
  </div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message" data-post="news_crypto/1">
    <div class="tgme_widget_message_forwarded_from accent_color">Forwarded from&nbsp;
      <span class="tgme_widget_message_forwarded_from_name"><span dir="auto"></span></span></div>
    <div class="tgme_widget_message_text js-message_text"><b>JUST IN : </b>Two critical PHP flaws
      <a href="https://t.me/Fin_Watch" target="_blank">@Fin_Watch</a></div>
    <div class="tgme_widget_message_footer">
      <span class="tgme_widget_message_views">3.1K</span>
      <a class="tgme_widget_message_date" href="https://t.me/news_crypto/1">
        <time datetime="2026-10-21T19:00:00+00:00"></time></a>
    </div>
  </div>
</div>
"""


def test_parse_page_extracts_metadata_and_posts():
    page = parse.parse_channel_page(PAGE, "news_crypto")
    assert page.title == "Crypto News"
    assert page.subscribers == 99_800
    assert [p["message_id"] for p in page.posts] == [1, 2]
    newest, oldest = page.posts[1], page.posts[0]
    assert newest["views"] == 6_440
    assert newest["links"] == ["https://example.com/etf"]
    assert newest["timestamp"].startswith("2026-10-21T20:13")
    assert oldest["forward_from"] == "Fin_Watch"
    assert page.posts[0]["url"] == "https://t.me/news_crypto/1"


def test_parse_count_handles_suffixes():
    assert parse.parse_count("6.44K") == 6_440
    assert parse.parse_count("1.2M") == 1_200_000
    assert parse.parse_count(None) is None


def _post(message_id: int, text: str, **extra):
    base = {
        "channel": "news_crypto",
        "message_id": message_id,
        "timestamp": "2026-10-21T20:13:03+00:00",
        "text": text,
        "views": 10,
        "forward_from": None,
        "links": [],
        "url": f"https://t.me/news_crypto/{message_id}",
    }
    base.update(extra)
    return base


def test_append_posts_is_idempotent_and_dedupes_text(tmp_path):
    posts = classify.annotate(
        [
            _post(2, "Bitcoin ETF inflows hit a record high today", links=["https://example.com/etf"]),
            _post(1, "Bitcoin ETF inflows hit a record high today, again"),
        ],
        "aggregator",
    )
    first = store.append_posts("news_crypto", posts, tmp_path)
    assert first == {"appended": 2, "skipped": 0}
    again = store.append_posts("news_crypto", posts, tmp_path)
    assert again == {"appended": 0, "skipped": 2}
    dup_text = classify.annotate([_post(3, "Bitcoin ETF inflows hit a record high today, again")], "aggregator")
    assert store.append_posts("news_crypto", dup_text, tmp_path)["appended"] == 0
    rows = (tmp_path / "news_crypto.jsonl").read_text().strip().splitlines()
    assert len(rows) == 2
    assert json.loads(rows[0])["text_hash"]


def test_classification_rewards_links_and_penalises_forwards():
    linked = classify.score_post(_post(1, "x" * 120, links=["https://example.com/a"]), "unknown")
    forwarded = classify.score_post(
        _post(2, "JUST IN : short", forward_from="Fin_Watch", links=["https://t.me/Fin_Watch"]),
        "aggregator",
    )
    assert linked["has_link"] is True
    assert linked["trust_score"] > forwarded["trust_score"]
    assert forwarded["is_forward"] is True
    assert forwarded["has_link"] is False
    assert forwarded["trust_band"] == "low"
    profile = classify.channel_profile(classify.annotate([forwarded, forwarded], "aggregator"), "aggregator")
    assert profile["kind"] == "aggregator"
    assert profile["forward_share"] == 1.0


def test_databus_adapter_dedupes_across_channels(tmp_path):
    a = store.append_posts(
        "news_crypto",
        classify.annotate([_post(5, "Same body across channels")], "aggregator"),
        tmp_path,
    )
    b = store.append_posts(
        "Fin_Watch",
        classify.annotate([_post(9, "Same body across channels", channel="Fin_Watch")], "primary"),
        tmp_path,
    )
    assert a["appended"] == 1
    assert b["appended"] == 1
    articles = databus.telegram_news_articles(limit=10, max_age_hours=10_000, root=tmp_path)
    assert len(articles) == 1
    assert articles[0]["source"].startswith("Telegram @")
    assert "trust_score" in articles[0]
    assert "trust_band" in articles[0]


def test_unknown_kind_derives_role_and_carries_trust_weight():
    dark = classify.annotate([_post(1, "x" * 120), _post(2, "y" * 120)], "unknown")
    profile = classify.channel_profile(dark, "unknown")
    assert profile["kind"] == "aggregator"  # link_share == 0.0 -> derived
    assert profile["trust_weight"] == 0.35
    linked = classify.annotate(
        [_post(3, "z" * 120, links=["https://example.com/a"])], "primary"
    )
    assert classify.channel_profile(linked, "primary")["trust_weight"] == 1.0
