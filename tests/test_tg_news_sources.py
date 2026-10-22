"""Source-table invariants for the Telegram security/scam sweep (offline).

These lock the 2026-10-23 probe findings into CI, because the failure mode was
silent: a squatted lookalike ("Certik Alert (Meme)", 48 subs) and a parked
"For sale" page were fed to Rugbrain as 0.9 security intel, and a closed
handle produced an empty JSONL with no error.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.databus.tg_news import bridge
from app.databus.tg_news.channels import channel_list
from app.databus.tg_news.verified_channels import (
    FALLBACK,
    GAPS,
    IMPOSTOR,
    PREVIEW_OFF,
    ROLES,
    SECURITY_CLOSED,
    SWEPT,
    WEIGHTS,
)


def test_every_closed_handle_is_carried_or_declared_a_gap() -> None:
    """No silent drop: 302 => web/X fallback, or an explicit gap."""
    uncovered = {name for name in SECURITY_CLOSED if name not in FALLBACK and name not in GAPS}
    assert uncovered == set()
    assert SECURITY_CLOSED <= PREVIEW_OFF, "a security handle cannot be both open and closed"


def test_no_impostor_is_swept_or_weighted() -> None:
    swept = {channel.username.lower() for channel in SWEPT}
    assert swept & IMPOSTOR == set()
    assert set(ROLES) & IMPOSTOR == set()


def test_zachxbt_is_swept_as_a_security_desk() -> None:
    rows = {channel.username.lower(): channel for channel in SWEPT}
    assert rows["zachxbt"].role == "security"
    assert WEIGHTS["zachxbt"] >= 0.9
    assert rows["zachxbt"].subs is not None
    assert rows["zachxbt"].subs > 100_000


def test_weights_stay_inside_the_rubric() -> None:
    assert all(0.0 <= weight <= 1.0 for weight in WEIGHTS.values())
    assert WEIGHTS["zachxbt"] > WEIGHTS["slowmistteam"], "a dormant channel must not out-rank a live one"


def test_channel_list_skips_impostors_and_closed_handles(monkeypatch) -> None:
    """Registry extras cannot reintroduce a squat or a 302 handle."""

    class _Entry(dict):
        pass

    monkeypatch.setattr(
        "app.databus.tg_channel_registry.enabled_channels",
        lambda: [
            _Entry(username="CertiKAlert", kind="news"),
            _Entry(username="marginfi", kind="news"),
            _Entry(username="brand_new_desk", kind="news"),
        ],
    )
    handles = {seed.username.lower() for seed in channel_list()}
    assert "certikalert" not in handles
    assert "marginfi" not in handles
    assert "brand_new_desk" in handles


def test_bridge_row_carries_all_three_source_fields() -> None:
    post = {
        "message_id": 7,
        "timestamp": "2026-10-20T10:00:00+00:00",
        "text": "drainer took $2M",
        "url": "https://t.me/zachxbt/7",
    }
    row = bridge.rows([post], "zachxbt", datetime.now(UTC))[0]
    assert row[15] == "telegram"
    assert row[9] == 0.95  # source_trust: curated channel weight
    assert 0 < row[10] <= row[9]  # source_weight <= trust
    assert row[14] == "scam"


def test_bridge_row_drops_stale_posts() -> None:
    post = {"message_id": 1, "timestamp": "2022-01-01T00:00:00+00:00", "text": "old"}
    assert bridge.rows([post], "slowmistteam", datetime.now(UTC)) == []


def test_doc_id_is_stable_uuid5() -> None:
    first = bridge.doc_id("zachxbt", 109)
    assert first == bridge.doc_id("ZachXBT", 109)
    assert uuid.UUID(first.rsplit(":", 1)[-1]).version == 5


def test_non_security_channel_is_news_not_security() -> None:
    post = {"message_id": 3, "timestamp": "2026-10-20T10:00:00+00:00", "text": "ETF inflows"}
    row = bridge.rows([post], "cointelegraph", datetime.now(UTC))[0]
    assert row[14] == "news"
