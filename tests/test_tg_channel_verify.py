"""Pure tests for the Telegram-channel liveness probe (app.databus.tg_channel_verify).

No network access: every probe runs through an injected fake async client with
canned httpx-like responses. The seed registry under ``app/databus`` is never
touched; ``write_status`` tests use ``tmp_path``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.databus import tg_channel_verify as tcv

PREVIEW_BODY = (
    "<html><head><title>CoinTelegraph</title>"
    '<link rel="canonical" href="https://t.me/cointelegraph">'
    '</head><body class="tgme_page">'
    '<div class="tgme_channel_info"><span>channel preview</span></div>'
    "</body></html>"
)
NOT_FOUND_BODY = "<html><body>Sorry, this page doesn't exist.</body></html>"


@dataclass
class FakeResponse:
    status_code: int = 200
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class FakeClient:
    """Minimal async httpx.AsyncClient stand-in returning canned responses."""

    def __init__(self, responses: dict[str, FakeResponse] | None = None, error: Exception | None = None):
        self.responses = responses or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.responses.get(url, FakeResponse(status_code=404, text=NOT_FOUND_BODY))


def _client(*, responses: dict[str, FakeResponse] | None = None, error: Exception | None = None) -> FakeClient:
    return FakeClient(responses=responses, error=error)


def _preview(username: str, title: str = "Preview") -> FakeResponse:
    return FakeResponse(
        status_code=200,
        text=(
            f"<html><head><title>{title}</title>"
            f'<link rel="canonical" href="https://t.me/{username}">'
            f'</head><body class="tgme_page">'
            f'<div class="tgme_channel_info"><span>channel preview</span></div>'
            "</body></html>"
        ),
    )


def _entry(username: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "username": username,
        "name": f"{username} News",
        "kind": "news",
        "category": "crypto_news",
        "reliable": True,
        "scrape_times_per_day": 8,
        "notes": "fixture",
        "enabled": True,
    }
    base.update(overrides)
    return base


# ─── probe_channel ────────────────────────────────────────────────────────────


async def test_probe_live_on_200_preview() -> None:
    client = _client(responses={"https://t.me/cointelegraph": _preview("cointelegraph", "CoinTelegraph")})
    result = await tcv.probe_channel("cointelegraph", client=client)
    assert result["live"] is True
    assert result["username"] == "cointelegraph"
    assert result["resolved_username"] == "cointelegraph"
    assert result["title"] == "CoinTelegraph"
    assert result["error"] is None
    assert client.calls[0][0] == "https://t.me/cointelegraph"


async def test_probe_dead_on_redirect_to_root() -> None:
    client = _client(
        responses={
            "https://t.me/nope123": FakeResponse(status_code=302, headers={"location": "https://t.me/"}, text="")
        }
    )
    result = await tcv.probe_channel("nope123", client=client)
    assert result["live"] is False
    assert result["resolved_username"] is None
    assert result["title"] is None


async def test_probe_dead_on_404() -> None:
    client = _client(responses={"https://t.me/gone456": FakeResponse(status_code=404, text=NOT_FOUND_BODY)})
    result = await tcv.probe_channel("gone456", client=client)
    assert result["live"] is False
    assert result["error"]


async def test_probe_fail_open_on_exception() -> None:
    client = _client(error=httpx.ConnectError("connection refused"))
    result = await tcv.probe_channel("broken", client=client)
    assert result["live"] is False
    assert result["error"]  # error message surfaced, no exception propagated


async def test_probe_live_on_redirect_to_channel_preview() -> None:
    client = _client(
        responses={
            "https://t.me/privatechan": FakeResponse(
                status_code=302, headers={"location": "https://t.me/s/privatechan"}, text=""
            )
        }
    )
    result = await tcv.probe_channel("privatechan", client=client)
    assert result["live"] is True
    assert result["resolved_username"] == "privatechan"


async def test_probe_empty_username() -> None:
    result = await tcv.probe_channel("  ", client=_client())
    assert result["live"] is False
    assert result["error"]


# ─── verify_registry ──────────────────────────────────────────────────────────


async def test_verify_registry_buckets_live_dead_and_skips_disabled() -> None:
    client = _client(
        responses={
            "https://t.me/live1": _preview("live1"),
            "https://t.me/live2": _preview("live2"),
            "https://t.me/dead1": FakeResponse(status_code=302, headers={"location": "https://t.me/"}, text=""),
            "https://t.me/broken1": FakeResponse(status_code=404, text=NOT_FOUND_BODY),
        }
    )
    channels = [
        _entry("live1", kind="news", reliable=True, notes="official"),
        _entry("live2", kind="intel", reliable=False, notes="recheck"),
        _entry("dead1", kind="scammer_caller", reliable=True, notes="flagged"),
        _entry("broken1", kind="news", reliable=True, notes="fixture"),
        _entry("offchan", enabled=False),  # must NOT be probed
    ]
    results = await tcv.verify_registry(channels, client=client, concurrency=2)

    assert results["summary"] == {"total": 4, "live": 2, "dead": 2}
    verified = {r["username"] for r in results["verified"]}
    dead = {r["username"] for r in results["dead"]}
    assert verified == {"live1", "live2"}
    assert dead == {"dead1", "broken1"}

    # each result preserves kind/reliable/notes from the registry entry
    by_name = {r["username"]: r for r in [*results["verified"], *results["dead"]]}
    assert by_name["live1"]["kind"] == "news"
    assert by_name["live1"]["reliable"] is True
    assert by_name["live1"]["notes"] == "official"
    assert by_name["live2"]["reliable"] is False
    assert by_name["dead1"]["kind"] == "scammer_caller"
    assert by_name["broken1"]["error"]

    probed_urls = [url for url, _ in client.calls]
    assert "https://t.me/offchan" not in probed_urls


async def test_verify_registry_empty_channel_list() -> None:
    results = await tcv.verify_registry([])
    assert results["summary"] == {"total": 0, "live": 0, "dead": 0}
    assert results["verified"] == []
    assert results["dead"] == []


async def test_verify_registry_creates_and_closes_client_when_none() -> None:
    # No network: client is constructed lazily and closed without any request.
    results = await tcv.verify_registry([_entry("alpha", enabled=False)])
    assert results["summary"]["total"] == 0


# ─── write_status ─────────────────────────────────────────────────────────────


def test_write_status_round_trip(tmp_path) -> None:
    results = {
        "verified": [{"username": "live1", "live": True, "kind": "news", "reliable": True, "notes": "official"}],
        "dead": [
            {
                "username": "dead1",
                "live": False,
                "kind": "scammer_caller",
                "reliable": True,
                "notes": "flagged",
                "error": "redirected to t.me root (handle not found)",
            }
        ],
        "summary": {"total": 2, "live": 1, "dead": 1},
    }
    path = tmp_path / "tg_channels_status.json"
    payload = tcv.write_status(results, str(path))

    assert "error" not in payload
    assert payload["summary"] == results["summary"]
    assert path.exists()
    with open(path) as f:
        stored = json.load(f)
    assert stored["summary"] == results["summary"]
    assert len(stored["channels"]) == 2
    first = stored["channels"][0]
    assert first["username"] == "live1"
    assert first["live"] is True
    assert first["probed_at"]  # ISO timestamp present
    assert stored["channels"][1]["username"] == "dead1"
    assert stored["channels"][1]["live"] is False
    assert stored["channels"][1]["error"]


def test_write_status_fail_open_on_bad_path(tmp_path) -> None:
    results = {"verified": [], "dead": [], "summary": {"total": 0, "live": 0, "dead": 0}}
    payload = tcv.write_status(results, str(tmp_path))  # a directory, not a file
    assert "error" in payload
    assert payload["error"]


# ─── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_requires_probe_flag() -> None:
    assert tcv.main([]) == 2  # prints help, no network
