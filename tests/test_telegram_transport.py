"""Pure unit tests for the Telegram alert/news transport.

No network, no DB, no Telethon: the headless client is replaced with an
``AsyncMock`` and ``CHANNEL_IDS`` is monkeypatched per test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.domains.alerts import telegram_transport as transport
from app.domains.alerts.models import PlatformAlert
from app.domains.alerts.publisher import route_alerts


def _alert(
    *,
    severity: str = "high",
    category: str = "rug-risk",
    title: str = "Rug pull detected",
    body: str = "Liquidity removed from pool.",
    token_address: str = "0xABC",  # noqa: S107 — fixture helper, not a credential
    chain: str = "ethereum",
    source: str = "scanner",
) -> PlatformAlert:
    return PlatformAlert(
        severity=severity,
        category=category,
        title=title,
        body=body,
        token_address=token_address,
        chain=chain,
        source=source,
    )


# ── CHANNEL_IDS resolution ────────────────────────────────────────────────


class TestChannelIdsFromEnv:
    def test_parses_numeric_ids(self) -> None:
        ids = transport.channel_ids_from_env(
            {
                "RMI_ALERTS_CHANNEL_ID": "-1001234567890",
                "RMI_CRYPTONEWS_CHANNEL_ID": "987654321",
                "RMI_MAIN_CHANNEL_ID": "-1005555",
            }
        )
        assert ids["rmialerts"] == -1001234567890
        assert ids["rmicryptonews"] == 987654321
        assert ids["main_channel"] == -1005555

    def test_alerts_channel_defaults_to_handle(self) -> None:
        ids = transport.channel_ids_from_env({})
        assert ids["alerts_channel"] == "@RugMunchAlerts"

    def test_missing_ids_resolve_to_none(self) -> None:
        ids = transport.channel_ids_from_env({"RMI_ALERTS_CHANNEL": ""})
        assert ids["rmialerts"] is None
        assert ids["rmicryptonews"] is None
        assert ids["main_channel"] is None

    def test_empty_value_is_none(self) -> None:
        ids = transport.channel_ids_from_env({"RMI_MAIN_CHANNEL_ID": "   "})
        assert ids["main_channel"] is None


# ── Formatting ─────────────────────────────────────────────────────────────


class TestFormatAlertMessage:
    def test_escapes_html_and_includes_severity_token_source(self) -> None:
        alert = _alert(
            title="Rug <pull> & honeypot",
            body='Body with <script>alert("x")</script> & stuff',
            token_address="0xABC&<token>",
        )
        text = transport.format_alert_message(alert)

        # Title and body escaped — no raw HTML injection
        assert "&lt;pull&gt; &amp; honeypot" in text
        assert "&lt;script&gt;" in text
        assert "<script>" not in text
        assert "&lt;token&gt;" in text
        assert "0xABC&amp;" in text

        # Severity emoji + labels, token, chain, source present
        assert "🟠" in text
        assert "HIGH" in text
        assert "Token:" in text
        assert "Chain: ethereum" in text
        assert "Source: scanner" in text

    def test_critical_uses_red_emoji(self) -> None:
        assert "🔴" in transport.format_alert_message(_alert(severity="critical"))

    def test_includes_links(self) -> None:
        alert = _alert()
        alert.urls = ["https://example.com/token?x=1&y=2"]
        text = transport.format_alert_message(alert)
        assert '<a href="https://example.com/token?x=1&amp;y=2">' in text

    def test_omits_absent_token_and_chain(self) -> None:
        text = transport.format_alert_message(_alert(token_address=None, chain=None))
        assert "Token:" not in text
        assert "Chain:" not in text


class TestFormatNewsMessage:
    def test_shape_has_headline_source_link_summary(self) -> None:
        text = transport.format_news_message(
            {
                "title": "Whale <buys> $5M of SHIB",
                "summary": "A large wallet moved 50B SHIB\nfrom Coinbase.",
                "link": "https://example.com/news/1",
                "source": "On-chain Intel",
            }
        )
        assert "<b>Whale &lt;buys&gt; $5M of SHIB</b>" in text
        assert "A large wallet moved 50B SHIB from Coinbase." in text  # collapsed to one line
        assert "Source: On-chain Intel" in text
        assert '<a href="https://example.com/news/1">' in text

    def test_news_article_model_shape_accepted(self) -> None:
        text = transport.format_news_message(
            {
                "title": "Headline",
                "body": "Summary text",
                "source_channel": "rmicryptonews",
                "source_url": "https://t.me/rmicryptonews/42",
            }
        )
        assert "Headline" in text
        assert "Summary text" in text
        assert "https://t.me/rmicryptonews/42" in text


# ── publish_routed ─────────────────────────────────────────────────────────


@pytest.fixture
def plan() -> list[dict]:
    critical = _alert(severity="critical", title="Critical alert")
    high = _alert(severity="high", title="High alert")
    return route_alerts([critical, high])


@pytest.fixture
def fake_client() -> AsyncMock:
    return AsyncMock(send_message=AsyncMock(return_value=True))


class TestPublishRouted:
    async def test_sends_only_to_channels_with_ids(
        self, plan: list[dict], fake_client: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # alerts_channel + rmialerts configured; main_channel/rmicryptonews missing
        monkeypatch.setattr(
            transport,
            "CHANNEL_IDS",
            {"alerts_channel": 111, "rmialerts": 222, "rmicryptonews": None, "main_channel": None},
        )

        summary = await transport.publish_routed(plan, client=fake_client)

        assert summary["sent"] == 4  # critical+high → alerts_channel, critical+high → rmialerts
        # site (2 alerts, no Telegram surface) + main_channel (1 critical, no id)
        assert summary["skipped"] == 3
        assert summary["errors"] == []

        sent_chats = [c.args[0] for c in fake_client.send_message.await_args_list]
        assert sorted(sent_chats) == [111, 111, 222, 222]
        for call in fake_client.send_message.await_args_list:
            assert call.kwargs["parse_mode"] == "html"

    async def test_severity_routing_critical_to_main_high_to_rmialerts(
        self, plan: list[dict], fake_client: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            transport,
            "CHANNEL_IDS",
            {"alerts_channel": 111, "rmialerts": 222, "rmicryptonews": None, "main_channel": 333},
        )

        summary = await transport.publish_routed(plan, client=fake_client)

        assert summary["sent"] == 5
        assert summary["skipped"] == 2  # site has no Telegram surface

        sent_chats = [c.args[0] for c in fake_client.send_message.await_args_list]
        # critical → alerts_channel(111), rmialerts(222), main_channel(333)
        assert sent_chats.count(333) == 1
        # high → alerts_channel + rmialerts only (NOT main_channel)
        assert sent_chats.count(111) == 2
        assert sent_chats.count(222) == 2

        # The critical message is the one sent to main_channel
        main_text = next(c.args[1] for c in fake_client.send_message.await_args_list if c.args[0] == 333)
        assert "Critical alert" in main_text

    async def test_fails_open_on_raising_client(self, plan: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            transport,
            "CHANNEL_IDS",
            {"alerts_channel": 111, "rmialerts": 222, "rmicryptonews": None, "main_channel": None},
        )
        client = AsyncMock(send_message=AsyncMock(side_effect=[RuntimeError("boom"), True, True, True]))

        summary = await transport.publish_routed(plan, client=client)

        assert summary["sent"] == 3
        assert summary["skipped"] == 3  # site (2) + main_channel (1, no id)
        assert len(summary["errors"]) == 1
        assert "boom" in summary["errors"][0]

    async def test_false_return_recorded_as_error(self, plan: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            transport,
            "CHANNEL_IDS",
            {"alerts_channel": 111, "rmialerts": None, "rmicryptonews": None, "main_channel": None},
        )
        client = AsyncMock(send_message=AsyncMock(return_value=False))

        summary = await transport.publish_routed(plan, client=client)

        assert summary["sent"] == 0
        # site (2), rmialerts (2, no id), main_channel (1, no id)
        assert summary["skipped"] == 5
        assert len(summary["errors"]) == 2
        assert all("returned False" in e for e in summary["errors"])

    async def test_skips_channel_with_no_id(
        self, plan: list[dict], fake_client: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            transport,
            "CHANNEL_IDS",
            {"alerts_channel": None, "rmialerts": None, "rmicryptonews": None, "main_channel": None},
        )

        summary = await transport.publish_routed(plan, client=fake_client)

        # site (2) + all configured channels have no id (alerts 2, rmialerts 2, main 1)
        assert summary == {"sent": 0, "skipped": 7, "errors": []}
        fake_client.send_message.assert_not_awaited()


class TestPublishAlert:
    async def test_routes_single_alert_and_publishes(
        self, fake_client: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            transport,
            "CHANNEL_IDS",
            {"alerts_channel": 111, "rmialerts": 222, "rmicryptonews": None, "main_channel": None},
        )
        summary = await transport.publish_alert(_alert(severity="high"), client=fake_client)
        assert summary["sent"] == 2
        assert summary["skipped"] == 1  # site — no Telegram surface
        assert [c.args[0] for c in fake_client.send_message.await_args_list] == [111, 222]


# ── publish_news_to_channel ────────────────────────────────────────────────


class TestPublishNews:
    def _articles(self) -> list[dict]:
        return [
            {
                "title": "News A",
                "summary": "Summary A",
                "link": "https://example.com/a",
                "source": "Intel",
                "content_hash": "abc123",
            },
            {
                "title": "News A (duplicate)",
                "summary": "Summary A",
                "link": "https://example.com/a",
                "source": "Intel",
                "content_hash": "abc123",
            },
            {
                "title": "News B",
                "summary": "Summary B",
                "link": "https://example.com/b",
                "source": "Intel",
                "content_hash": "def456",
            },
        ]

    async def test_dedupes_by_content_hash(self, fake_client: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            transport,
            "CHANNEL_IDS",
            {"alerts_channel": 111, "rmialerts": 222, "rmicryptonews": 777, "main_channel": None},
        )
        summary = await transport.publish_news_to_channel(self._articles(), client=fake_client)
        assert summary == {"sent": 2, "skipped": 1, "errors": []}
        assert fake_client.send_message.await_count == 2
        assert all(c.args[0] == 777 for c in fake_client.send_message.await_args_list)
        assert all(c.kwargs["parse_mode"] == "html" for c in fake_client.send_message.await_args_list)

    async def test_derived_key_dedupes_without_content_hash(
        self, fake_client: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            transport,
            "CHANNEL_IDS",
            {"alerts_channel": 111, "rmialerts": 222, "rmicryptonews": 777, "main_channel": None},
        )
        articles = [
            {"title": "Same", "link": "https://example.com/x"},
            {"title": "Same", "link": "https://example.com/x"},
        ]
        summary = await transport.publish_news_to_channel(articles, client=fake_client)
        assert summary == {"sent": 1, "skipped": 1, "errors": []}

    async def test_skips_when_channel_unconfigured(
        self, fake_client: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            transport,
            "CHANNEL_IDS",
            {"alerts_channel": 111, "rmialerts": 222, "rmicryptonews": None, "main_channel": None},
        )
        summary = await transport.publish_news_to_channel(self._articles(), client=fake_client)
        assert summary == {"sent": 0, "skipped": 3, "errors": []}
        fake_client.send_message.assert_not_awaited()
