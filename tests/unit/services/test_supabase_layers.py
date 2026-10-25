"""Supabase persistence layer after the split: contract, boundaries, failure paths.

The service was split into single-purpose modules behind a facade. These tests
assert the two things the split must preserve:
  * the transport contract at the HTTP boundary (status -> return shape),
  * the facade invariant (every historical public name still resolves).
Domain modules are exercised through that same boundary, so a regression in
any layer surfaces as a changed observable result, not as an import error.
"""

from __future__ import annotations

import inspect
import os

os.environ.setdefault("JWT_SECRET", "a" * 32)
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("RMI_AUTH_TOKEN", "test-auth-token-32-chars-long!!")

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.services.supabase_service as facade
from app.services import supabase_transport as transport

BASE = "https://proj.supabase.co"

# The public surface that existed before the split (frozen contract).
FACADE_SURFACE = {
    "acknowledge_alert",
    "add_to_watchlist",
    "award_badge",
    "check_health",
    "create_alert",
    "create_bulletin_post",
    "ensure_x402_payments_table",
    "get_alerts",
    "get_badges",
    "get_bulletin_posts",
    "get_cluster_wallets",
    "get_contract_analysis",
    "get_entity_clusters",
    "get_market_data",
    "get_profile",
    "get_subscription",
    "get_syndicate_wallets",
    "get_token",
    "get_tokens",
    "get_transactions",
    "get_user_badges",
    "get_wallet",
    "get_wallet_connections",
    "get_wallet_intel",
    "get_wallets",
    "get_watchlist",
    "get_x402_earnings_summary",
    "get_x402_payment_by_tx",
    "get_x402_payments",
    "record_x402_payment_supabase",
    "remove_from_watchlist",
    "store_market_data",
    "track_event",
    "update_profile",
    "upsert_contract_analysis",
    "upsert_token",
    "upsert_wallet",
    "vote_post",
}

TRANSPORT_CALLS = ("get", "post", "patch", "delete")


def _client(status: int = 200, payload: object = None) -> tuple[MagicMock, AsyncMock]:
    """Build a patched httpx.AsyncClient context manager answering with one response."""
    inst = AsyncMock()
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = payload
    for call in TRANSPORT_CALLS:
        getattr(inst, call).return_value = resp
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=inst)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, inst


# ── Transport contract ────────────────────────────────────────────────


async def test_get_builds_filter_order_limit_query():
    with patch("app.services.supabase_transport._get_url", return_value=BASE) as url:
        ctx, inst = _client(200, [{"id": 1}])
        with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx):
            rows = await transport._get(
                "alerts", {"severity": "high"}, order="created_at.desc", limit=5
            )
    assert rows == [{"id": 1}]
    called = inst.get.await_args.args[0]
    assert called.startswith(f"{BASE}/rest/v1/alerts?select=*")
    assert "severity=eq.high" in called
    assert "order=created_at.desc" in called
    assert "limit=5" in called
    url.assert_called()


@pytest.mark.parametrize("status", [400, 401, 404, 500, 503])
async def test_get_failures_return_empty_list(status: int):
    ctx, _ = _client(status, {"message": "boom"})
    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx):
        assert await transport._get("alerts") == []


async def test_post_returns_first_row_then_empty_dict_boundary():
    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(201, [{"id": 7}])[0]):
        assert await transport._post("alerts", {"a": 1}) == {"id": 7}
    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(201, [])[0]):
        assert await transport._post("alerts", {"a": 1}) == {}
    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(500, None)[0]):
        assert await transport._post("alerts", {"a": 1}) is None


@pytest.mark.parametrize(("status", "expected"), [(204, True), (200, True), (500, False)])
async def test_patch_and_delete_report_success_as_bool(status: int, expected: bool):
    ctx, _ = _client(status, None)
    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx):
        assert await transport._patch("alerts", {"id": "1"}, {"is_read": True}) is expected
    ctx, _ = _client(status, None)
    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx):
        assert await transport._delete("alerts", {"id": "1"}) is expected


async def test_rpc_returns_none_on_error_status():
    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(400, None)[0]):
        assert await transport._rpc("exec_sql", {"sql": "select 1"}) is None


async def test_service_key_falls_back_to_role_key_and_empty_last():
    def secrets(name: str, default: str = "") -> str:
        return {"SUPABASE_SERVICE_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": "role-key"}.get(
            name, default
        )

    with patch("app.services.supabase_transport.get_secret_or_none", side_effect=secrets):
        assert transport._get_key() == "role-key"
    with patch("app.services.supabase_transport.get_secret_or_none", return_value=""):
        assert transport._get_key() == ""


async def test_url_without_base_is_relative_boundary():
    with patch("app.services.supabase_transport._get_url", return_value=""):
        assert transport._url("wallets") == "/rest/v1/wallets"


# ── Facade invariant ─────────────────────────────────────────────────


def test_facade_preserves_public_surface():
    assert set(facade.__all__) - {"_supabase_cb"} == FACADE_SURFACE
    assert len(facade.__all__) == len(set(facade.__all__))
    for name in FACADE_SURFACE:
        assert callable(getattr(facade, name)), name
        assert inspect.iscoroutinefunction(getattr(facade, name)), name


def test_facade_reexports_origin_objects():
    from app.services import supabase_alerts, supabase_x402

    assert facade.get_alerts is supabase_alerts.get_alerts
    assert facade.get_x402_payments is supabase_x402.get_x402_payments


# ── Domain behaviour through the boundary ────────────────────────────


async def test_get_alerts_unread_filter_and_error_path():
    ctx, inst = _client(200, [{"id": "a1"}])
    with (
        patch("app.services.supabase_transport._get_url", return_value=BASE),
        patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx),
    ):
        assert await facade.get_alerts(severity="high", unread_only=True, limit=10) == [{"id": "a1"}]
    query = inst.get.await_args.args[0]
    assert "severity=eq.high" in query
    assert "is_read=eq.false" in query
    assert "limit=10" in query

    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(500, None)[0]):
        assert await facade.get_alerts() == []


async def test_acknowledge_alert_payload():
    ctx, inst = _client(204, None)
    with (
        patch("app.services.supabase_transport._get_url", return_value=BASE),
        patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx),
    ):
        assert await facade.acknowledge_alert("a1") is True
    assert inst.patch.await_args.kwargs["json"] == {"acknowledged": True, "is_read": True}


async def test_get_wallet_none_on_empty_boundary_and_row_on_hit():
    with patch("app.services.supabase_transport._get_url", return_value=BASE):
        with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(200, [])[0]):
            assert await facade.get_wallet("0xabc") is None
        with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(200, [{"address": "0xabc"}])[0]
        ):
            assert await facade.get_wallet("0xabc") == {"address": "0xabc"}


async def test_watchlist_delete_false_on_backend_error():
    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(500, None)[0]):
        assert await facade.remove_from_watchlist("u1", "0xabc") is False


async def test_vote_post_missing_row_is_false_without_write():
    with (
        patch("app.services.supabase_content._get", AsyncMock(return_value=[])),
        patch("app.services.supabase_content._patch", AsyncMock(return_value=True)) as write,
    ):
        assert await facade.vote_post(1, "up") is False
    write.assert_not_awaited()


async def test_vote_post_increments_from_missing_counter_boundary():
    with (
        patch("app.services.supabase_content._get", AsyncMock(return_value=[{"id": 1}])),
        patch("app.services.supabase_content._patch", AsyncMock(return_value=True)) as write,
    ):
        assert await facade.vote_post(1, "down") is True
    write.assert_awaited_once_with("bulletin_posts", {"id": 1}, {"downvotes": 1})


async def test_x402_summary_counts_only_fulfilled_and_bad_amounts():
    rows = [
        {
            "status": "fulfilled",
            "amount_atoms": "2000000",
            "chain": "base",
            "tool": "rug_shield",
            "created_at": "2026-10-01T00:00:00Z",
        },
        {
            "status": "pending",
            "amount_atoms": "9000000",
            "chain": "base",
            "tool": "rug_shield",
            "created_at": "2026-10-01T00:00:00Z",
        },
        {
            "status": "fulfilled",
            "amount_atoms": "not-a-number",
            "chain": "eth",
            "tool": "pulse",
            "created_at": "2026-10-02T00:00:00Z",
        },
    ]
    with patch("app.services.supabase_x402._get", AsyncMock(return_value=rows)):
        summary = await facade.get_x402_earnings_summary()
    assert summary["total_usdc"] == 2.0
    assert summary["by_chain_usdc"] == {"base": 2.0, "eth": 0.0}
    assert summary["record_count"] == 3


async def test_x402_summary_degrades_to_zeros_on_backend_error():
    with patch("app.services.supabase_x402._get", AsyncMock(side_effect=httpx.ConnectError("down"))):
        summary = await facade.get_x402_earnings_summary()
    assert summary == {
        "total_usdc": 0,
        "by_chain_usdc": {},
        "by_tool_usdc": {},
        "by_day_usdc": {},
        "record_count": 0,
    }


async def test_ensure_x402_table_false_on_http_error():

    with patch("app.services.supabase_x402.httpx.AsyncClient", return_value=_client(500, None)[0]):
        assert await facade.ensure_x402_payments_table() is False
# ── Wallets ──────────────────────────────────────────────────────────


async def test_get_wallets_filters_and_error_path():
    ctx, inst = _client(200, [{"address": "0x1"}])
    with (
        patch("app.services.supabase_transport._get_url", return_value=BASE),
        patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx),
    ):
        rows = await facade.get_wallets(chain="base", risk_min=10, risk_max=90, limit=7)
    assert rows == [{"address": "0x1"}]
    query = inst.get.await_args.args[0]
    assert "chain=eq.base" in query
    assert "risk_score=gte.10" in query
    assert "risk_score=lte.90" in query
    assert "limit=7" in query

    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(503, None)[0]):
        assert await facade.get_wallets() == []


async def test_upsert_wallet_sets_merge_prefer_and_none_boundary():
    ctx, inst = _client(200, [{"address": "0x1"}])
    with (
        patch("app.services.supabase_transport._get_url", return_value=BASE),
        patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx),
    ):
        assert await facade.upsert_wallet("0x1", "base", risk_score=42) == {"address": "0x1"}
    assert inst.post.await_args.kwargs["headers"]["Prefer"] == "resolution=merge-duplicates"
    assert inst.post.await_args.kwargs["json"] == {
        "address": "0x1",
        "chain": "base",
        "risk_score": 42,
    }

    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(201, [])[0]):
        assert await facade.upsert_wallet("0x1", "base") is None


# ── Content ──────────────────────────────────────────────────────────


async def test_get_user_badges_joins_details_and_skips_missing_rows():
    with (
        patch(
            "app.services.supabase_content._get",
            AsyncMock(return_value=[{"badge_id": "b1"}, {"badge_id": "missing"}]),
        ),
        patch(
            "app.services.supabase_content._get_in",
            AsyncMock(return_value=[{"id": "b1", "name": "OG"}]),
        ) as get_in,
    ):
        rows = await facade.get_user_badges("u1")
    assert rows == [{"badge_id": "b1", "badge_detail": {"id": "b1", "name": "OG"}}]
    get_in.assert_awaited_once_with("badges", "id", ["b1", "missing"])


async def test_get_user_badges_empty_short_circuits_lookup():
    with (
        patch("app.services.supabase_content._get", AsyncMock(return_value=[])),
        patch("app.services.supabase_content._get_in", AsyncMock()) as get_in,
    ):
        assert await facade.get_user_badges("u1") == []
    get_in.assert_not_awaited()


async def test_award_badge_payload_and_failure_path():
    with patch("app.services.supabase_content._post", AsyncMock(return_value={"id": 1})) as post:
        assert await facade.award_badge("u1", "b1", "manual") == {"id": 1}
    table, payload = post.await_args.args
    assert table == "user_badges"
    assert payload["user_id"] == "u1"
    assert payload["badge_id"] == "b1"
    assert payload["earned_via"] == "manual"
    assert payload["earned_at"].endswith("+00:00")

    with patch("app.services.supabase_content._post", AsyncMock(return_value=None)):
        assert await facade.award_badge("u1", "b1") is None


# ── Records ──────────────────────────────────────────────────────────


async def test_get_profile_none_on_empty_boundary():
    with patch("app.services.supabase_records._get", AsyncMock(return_value=[])):
        assert await facade.get_profile("u1") is None
    with patch("app.services.supabase_records._get", AsyncMock(return_value=[{"id": "u1"}])):
        assert await facade.get_profile("u1") == {"id": "u1"}


async def test_contract_analysis_upsert_payload_and_boundaries():
    with patch("app.services.supabase_records._get", AsyncMock(return_value=[])):
        assert await facade.get_contract_analysis("0xabc") is None

    ctx, inst = _client(201, [{"address": "0xabc"}])
    with (
        patch("app.services.supabase_transport._get_url", return_value=BASE),
        patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx),
    ):
        assert await facade.upsert_contract_analysis("0xabc", "base", score=9) == {
            "address": "0xabc"
        }
    body = inst.post.await_args.kwargs["json"]
    assert body["score"] == 9
    assert body["chain"] == "base"
    assert body["analyzed_at"].endswith("+00:00")
    assert inst.post.await_args.kwargs["headers"]["Prefer"] == "resolution=merge-duplicates"


async def test_cluster_wallets_missing_cluster_is_empty_boundary():
    with patch("app.services.supabase_records._get", AsyncMock(return_value=[])):
        assert await facade.get_cluster_wallets("c1") == []
    with patch(
        "app.services.supabase_records._get", AsyncMock(return_value=[{"addresses": ["0x1"]}])
    ):
        assert await facade.get_cluster_wallets("c1") == ["0x1"]


# ── Analytics ────────────────────────────────────────────────────────


async def test_track_event_payload_including_null_optionals():
    with patch("app.services.supabase_analytics._post", AsyncMock(return_value={"id": 1})) as post:
        assert await facade.track_event("scan") == {"id": 1}
    table, payload = post.await_args.args
    assert table == "analytics_events"
    assert payload == {
        "event_type": "scan",
        "user_id": None,
        "event_data": {},
        "page": None,
        "session_id": None,
    }


async def test_get_subscription_none_on_empty_boundary():
    with patch("app.services.supabase_analytics._get", AsyncMock(return_value=[])):
        assert await facade.get_subscription("u1") is None


# ── Transport IN filter and x402 write contract ──────────────────────


async def test_get_in_builds_in_filter_and_fails_closed():
    ctx, inst = _client(200, [{"id": 1}])
    with (
        patch("app.services.supabase_transport._get_url", return_value=BASE),
        patch("app.services.supabase_transport.httpx.AsyncClient", return_value=ctx),
    ):
        assert await transport._get_in("badges", "id", [1, 2]) == [{"id": 1}]
    assert "id=in.(1,2)" in inst.get.await_args.args[0]

    with patch("app.services.supabase_transport.httpx.AsyncClient", return_value=_client(500, None)[0]):
        assert await transport._get_in("badges", "id", [1]) == []


async def test_record_payment_stringifies_atoms_and_stamps_utc():
    with patch("app.services.supabase_x402._post", AsyncMock(return_value={"id": 1})) as post:
        await facade.record_x402_payment_supabase(
            "rug_shield", 2500000, "base", payer="0xp", tx_hash="0xh"
        )
    table, payload = post.await_args.args
    assert table == "x402_payments"
    assert payload["amount_atoms"] == "2500000"
    assert payload["status"] == "fulfilled"
    assert payload["created_at"].endswith("+00:00")
