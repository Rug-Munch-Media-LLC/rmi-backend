"""Pure tests for the Telegram channel-news ingester (no network, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.news_ingest.ingest import (
    channel_username_from_config,
    dedupe_articles,
    read_channel_messages,
    sweep_channels,
)
from app.domains.news_ingest.models import ChannelMessage, NewsArticle
from app.domains.news_ingest.normalize import (
    channel_message_to_article,
    extract_entities,
    strip_junk_lines,
)
from app.domains.news_ingest.scheduler import next_sweep_times, sweep_schedule

EVM_ADDR = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"
SOLANA_ADDR = "7EcDhMYGpCdUPmZkLPNmKkKQLM4mYvKX9vfsyxhMFfQ5"


def _msg(
    text: str,
    *,
    username: str = "cointelegraph",
    message_id: int = 1,
    forwarded: str | None = None,
) -> ChannelMessage:
    return ChannelMessage(
        channel="CoinTelegraph",
        channel_username=username,
        message_id=message_id,
        text=text,
        forwarded_from=forwarded,
        posted_at=datetime(2026, 8, 13, 10, 0, tzinfo=UTC),
        views=1200,
        reactions={"👍": 40},
    )


# ── extract_entities ───────────────────────────────────────────────────────


def test_extract_entities_symbols_uppercased() -> None:
    assert extract_entities("$BTC and $eth both pumping") == ["$BTC", "$ETH"]


def test_extract_entities_evm_address() -> None:
    assert EVM_ADDR in extract_entities(f"Token at {EVM_ADDR} just launched")


def test_extract_entities_solana_address() -> None:
    assert SOLANA_ADDR in extract_entities(f"CA: {SOLANA_ADDR}")


def test_extract_entities_dedup_and_order() -> None:
    text = f"$BTC {EVM_ADDR} more $BTC and {EVM_ADDR}"
    assert extract_entities(text) == ["$BTC", EVM_ADDR]


def test_extract_entities_empty() -> None:
    assert extract_entities("") == []
    assert extract_entities(None) == []  # type: ignore[arg-type]


def test_extract_entities_ignores_short_symbols() -> None:
    # $A (single char) and $ with trailing punctuation are not symbols.
    assert extract_entities("price is $1.20, token $A") == []


# ── strip_junk_lines ───────────────────────────────────────────────────────


def test_strip_junk_lines_removes_promo_and_bare_links() -> None:
    text = "Real headline about Bitcoin ETF\nJOIN OUR CHANNEL NOW\nhttps://t.me/spam"
    cleaned = strip_junk_lines(text)
    assert cleaned == "Real headline about Bitcoin ETF"
    assert "JOIN" not in cleaned
    assert "t.me" not in cleaned


# ── channel_message_to_article ─────────────────────────────────────────────


def test_channel_message_to_article_ok() -> None:
    article = channel_message_to_article(
        _msg(
            "Bitcoin price jumps 8% as ETF inflows hit a record high today\n\n"
            "Institutional demand keeps climbing. $BTC leads the rally with "
            f"strong volume. Contract: {EVM_ADDR}"
        ),
        category="crypto_news",
    )
    assert article is not None
    assert article.title.startswith("Bitcoin price jumps 8%")
    assert article.source_channel == "CoinTelegraph"
    assert article.source_url == "https://t.me/cointelegraph/1"
    assert article.category == "crypto_news"
    assert article.content_hash
    assert "$BTC" in article.entities
    assert EVM_ADDR in article.entities
    assert article.posted_at is not None


def test_channel_message_to_article_junk_skipped() -> None:
    assert channel_message_to_article(_msg("JOIN OUR CHANNEL NOW"), "crypto_news") is None


def test_channel_message_to_article_promo_skipped() -> None:
    assert (
        channel_message_to_article(
            _msg("BUY NOW BEFORE MOON 🚀\nt.me/rugpromo\nSIGNAL ALPHA fast"),
            "crypto_news",
        )
        is None
    )


def test_channel_message_to_article_empty_skipped() -> None:
    assert channel_message_to_article(_msg(""), "crypto_news") is None
    assert channel_message_to_article(_msg("   "), "crypto_news") is None


def test_channel_message_to_article_link_only_skipped() -> None:
    assert channel_message_to_article(_msg("https://example.com/story"), "crypto_news") is None


def test_channel_message_to_article_short_entity_free_skipped() -> None:
    assert channel_message_to_article(_msg("big news coming"), "crypto_news") is None


def test_channel_message_to_article_forward_noise_skipped() -> None:
    text = "The SEC just delayed the decision on the Bitcoin ETF again. Details inside."
    assert len(text) > 60  # long enough to pass the length gate
    msg = _msg(text, forwarded="Some Channel")
    assert channel_message_to_article(msg, "crypto_news") is None


def test_channel_message_to_article_forward_substantive_kept() -> None:
    text = (
        "The SEC just delayed the decision on the Bitcoin ETF again, citing "
        "market conditions. Analysts expect a final ruling by Q4. "
        "Institutional inflows keep climbing."
    )
    msg = _msg(text, forwarded="Some Channel")
    article = channel_message_to_article(msg, "crypto_news")
    assert article is not None
    assert article.title.startswith("The SEC just delayed")
    assert article.body == text


def test_channel_message_to_article_none_input() -> None:
    assert channel_message_to_article(None, "crypto_news") is None  # type: ignore[arg-type]


# ── dedupe_articles ────────────────────────────────────────────────────────


def test_dedupe_articles_content_hash_collapse() -> None:
    first = channel_message_to_article(_msg("Bitcoin tops $70k as ETFs keep buying\n\n" * 5), "crypto_news")
    second = channel_message_to_article(_msg("Bitcoin tops $70k as ETFs keep buying\n\n" * 5), "crypto_news")
    assert first is not None
    assert second is not None
    result = dedupe_articles([first, second])
    assert len(result) == 1
    assert result[0] is first


def test_dedupe_articles_source_url_fallback() -> None:
    a = NewsArticle(
        title="Same URL",
        body="different bodies but same url",
        source_channel="ch1",
        source_url="https://t.me/ch1/9",
    )
    b = NewsArticle(
        title="Same URL copy",
        body="another body, still same url",
        source_channel="ch1",
        source_url="https://t.me/ch1/9",
    )
    assert len(dedupe_articles([a, b])) == 1


def test_dedupe_articles_distinct_kept() -> None:
    a = channel_message_to_article(_msg("Ethereum staking yield rises to 4.5% this quarter\n\n" * 4), "defi")
    b = channel_message_to_article(_msg("Solana breaks new ATH after upgrade announcement\n\n" * 4), "crypto_news")
    assert a is not None
    assert b is not None
    assert len(dedupe_articles([a, b])) == 2


def test_dedupe_articles_empty_and_order() -> None:
    assert dedupe_articles([]) == []
    a = channel_message_to_article(_msg("First headline about the merge\n\n" * 4), "crypto_news")
    b = channel_message_to_article(_msg("Second headline about the merge\n\n" * 4), "crypto_news")
    assert a is not None
    assert b is not None
    result = dedupe_articles([b, a])
    assert result == [b, a]


# ── read_channel_messages (fake clients) ───────────────────────────────────


class FakeChannelReader:
    """Injectable fake exposing the ``read_messages`` protocol."""

    def __init__(self, messages_by_channel: dict[str, list[ChannelMessage]]) -> None:
        self.messages_by_channel = messages_by_channel
        self.calls: list[tuple[str, int]] = []

    async def read_messages(self, channel_username: str, limit: int = 50) -> list[ChannelMessage]:
        self.calls.append((channel_username, limit))
        return list(self.messages_by_channel.get(channel_username, []))


class RaisingChannelReader:
    async def read_messages(self, channel_username: str, limit: int = 50) -> list[ChannelMessage]:
        raise RuntimeError("telegram unavailable")


class _FakeTelethonMessage:
    """Duck-typed Telethon Message stand-in for the iter_messages path."""

    def __init__(
        self,
        *,
        id: int,
        message: str,
        media=None,
        fwd_from=None,
        views: int | None = None,
        reactions=None,
        chat_title: str = "CoinTelegraph",
    ) -> None:
        self.id = id
        self.message = message
        self.date = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
        self.media = media
        self.fwd_from = fwd_from
        self.views = views
        self.reactions = reactions

        class _Chat:
            title = chat_title

        self.chat = _Chat()


class TelethonStyleClient:
    """Fake mimicking the Telethon ``iter_messages`` surface."""

    def __init__(self, messages: list[object]) -> None:
        self.messages = messages

    async def iter_messages(self, entity: str, limit: int = 50):
        for message in self.messages[:limit]:
            yield message


async def test_read_channel_messages_fake_protocol() -> None:
    canned = [_msg("Bitcoin ETF decision imminent, analysts say\n\n" * 4)]
    client = FakeChannelReader({"cointelegraph": canned})
    messages = await read_channel_messages(client, "cointelegraph", limit=20)
    assert messages == canned
    assert client.calls == [("cointelegraph", 20)]


async def test_read_channel_messages_fake_empty() -> None:
    client = FakeChannelReader({})
    assert await read_channel_messages(client, "unknown", limit=10) == []


async def test_read_channel_messages_never_raises() -> None:
    assert await read_channel_messages(RaisingChannelReader(), "cointelegraph", limit=10) == []


async def test_read_channel_messages_none_client() -> None:
    assert await read_channel_messages(None, "cointelegraph", limit=10) == []
    assert await read_channel_messages(FakeChannelReader({}), "", limit=10) == []


async def test_read_channel_messages_telethon_style() -> None:
    client = TelethonStyleClient(
        [
            _FakeTelethonMessage(id=101, message="Solana breaks ATH on new upgrade\n\n" * 4, views=999),
            _FakeTelethonMessage(
                id=102,
                message="short forward",
                fwd_from=type("Fwd", (), {"from_name": "Other Channel"})(),
            ),
        ]
    )
    messages = await read_channel_messages(client, "solana_news", limit=50)
    assert len(messages) == 2
    assert messages[0].message_id == 101
    assert messages[0].channel_username == "solana_news"
    assert messages[0].views == 999
    assert messages[0].media_type == "text"
    assert messages[1].forwarded_from == "Other Channel"


async def test_sweep_channels_orchestration() -> None:
    canned = {
        "cointelegraph": [
            _msg("Bitcoin ETF decision imminent, analysts say\n\n" * 4, username="cointelegraph", message_id=11),
            _msg("JOIN OUR CHANNEL NOW", username="cointelegraph", message_id=12),
        ],
        "defi_news": [
            _msg("Aave v4 goes live on mainnet with new risk engine\n\n" * 4, username="defi_news", message_id=7),
            _msg("Aave v4 goes live on mainnet with new risk engine\n\n" * 4, username="defi_news", message_id=8),
        ],
    }
    channels = [
        {"id": "cointelegraph", "category": "crypto_news"},
        {"username": "@defi_news", "category": "defi"},
        {"url": "https://t.me/no_username_yet", "category": "crypto_news"},
        {"id": "not_configured", "category": "crypto_news"},  # no messages
        {"category": "crypto_news"},  # no resolvable username → skipped
    ]
    articles = await sweep_channels(FakeChannelReader(canned), channels, limit=25)
    # 2 real articles (junk dropped), 1 duplicate collapsed → 2 unique.
    assert len(articles) == 2
    categories = {a.category for a in articles}
    assert categories == {"crypto_news", "defi"}


def test_channel_username_from_config() -> None:
    assert channel_username_from_config({"username": "@defi_news"}) == "defi_news"
    assert channel_username_from_config({"channel_username": "defi_news"}) == "defi_news"
    assert channel_username_from_config({"id": "cointelegraph"}) == "cointelegraph"
    assert channel_username_from_config({"url": "https://t.me/solana_news/123"}) == "solana_news"
    assert channel_username_from_config({"name": "No Username"}) == ""
    assert channel_username_from_config({}) == ""


# ── scheduler ──────────────────────────────────────────────────────────────


def test_sweep_schedule_layout() -> None:
    assert [t.strftime("%H:%M") for t in sweep_schedule(4)] == ["00:00", "06:00", "12:00", "18:00"]
    assert sweep_schedule(0) == []
    assert sweep_schedule(-1) == []
    assert len(sweep_schedule(48)) == 24  # clamped


def test_next_sweep_times_determinism() -> None:
    now = datetime(2026, 8, 13, 13, 30, tzinfo=UTC)
    first = next_sweep_times(4, UTC, now=now)
    second = next_sweep_times(4, UTC, now=now)
    assert first == second
    assert all(t.tzinfo is not None for t in first)


def test_next_sweep_times_utc_layout() -> None:
    now = datetime(2026, 8, 13, 13, 30, tzinfo=UTC)
    times = next_sweep_times(4, UTC, now=now)
    assert [t.strftime("%Y-%m-%d %H:%M") for t in times] == [
        "2026-08-13 18:00",
        "2026-08-14 00:00",
        "2026-08-14 06:00",
        "2026-08-14 12:00",
    ]
    assert all(t > now for t in times)


def test_next_sweep_times_timezone_aware() -> None:
    now = datetime(2026, 8, 13, 13, 30, tzinfo=UTC)
    times = next_sweep_times(2, UTC, now=now)
    assert len(times) == 2
    assert all(t.utcoffset() == UTC.utcoffset(None) for t in times)


def test_next_sweep_times_zero() -> None:
    assert next_sweep_times(0, UTC, now=datetime(2026, 8, 13, tzinfo=UTC)) == []


def test_next_sweep_times_naive_now_assumed_utc() -> None:
    times = next_sweep_times(1, UTC, now=datetime(2026, 8, 13, 5, 0))
    assert times[0].hour == 0  # next day's 00:00 sweep
    assert times[0].tzinfo is not None
