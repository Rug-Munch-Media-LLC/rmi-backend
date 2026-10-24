"""Service orchestration — fake client/provider, no network."""

from __future__ import annotations

from typing import Any

from app.domains.gmgn_intel.service import GMGNIntelService


class FakeClient:
    """Stands in for GMGNIntelClient; returns canned raw payloads."""

    def __init__(self, **payloads: Any) -> None:
        self.payloads = payloads

    async def trenches(self, chain: str, limit: int = 60) -> Any:
        return self.payloads.get("trenches")

    async def rank(self, chain: str, interval: str = "1h", limit: int = 100) -> Any:
        return self.payloads.get("rank")

    async def token_info(self, address: str, chain: str) -> Any:
        return self.payloads.get("info")

    async def token_security(self, address: str, chain: str) -> Any:
        return self.payloads.get("security")


class FakeProvider:
    api_key = "test-key"

    def __init__(self) -> None:
        self.wallet_calls: list[tuple[str, str, str]] = []

    async def get_wallet_detail(self, wallet: str, chain: str = "solana", period: str = "7d") -> dict[str, Any]:
        self.wallet_calls.append((wallet, chain, period))
        return {"wallet": wallet, "source": "gmgn", "realized_profit": 10, "winrate": 0.5, "activities": []}

    async def get_smart_money(self, address: str, chain: str = "solana") -> dict[str, Any]:
        return {"address": address, "source": "gmgn", "smart_money_activity": [{"wallet": "W"}]}


def _token_row(address: str, **extra: Any) -> dict[str, Any]:
    row = {"address": address, "chain": "sol", "symbol": "TST", "holder_count": 50, "liquidity": 9000}
    row.update(extra)
    return row


async def test_lifecycle_builds_counts_from_all_buckets() -> None:
    client = FakeClient(
        trenches={
            "new_creation": [_token_row("n1", progress=0.01)],
            "near_completion": [_token_row("a1", progress=0.95)],
            "completed": [_token_row("g1", complete_timestamp=1792828027)],
        },
        rank=[{"address": "g1", "rank": 1}],
    )
    result = await GMGNIntelService(client=client).lifecycle("sol", 60)
    assert result["counts"] == {"new": 1, "bonding": 0, "almost_graduated": 1, "graduated": 1, "trending": 1}
    assert result["token_count"] == 3
    assert result["sources"] == ["POST /v1/trenches", "GET /v1/market/rank"]


async def test_lifecycle_without_upstream_data_explains_itself() -> None:
    result = await GMGNIntelService(client=FakeClient()).lifecycle("solana")
    assert result["token_count"] == 0
    assert result["counts"]["graduated"] == 0
    assert result["missing"][0]["field"] == "trenches"


async def test_ranked_trending_uses_our_score_not_gmgn_order() -> None:
    weak = _token_row("weak", volume_24h=1500, liquidity=1200, holder_count=12, buys=1, sells=9, rank=1)
    strong = _token_row(
        "strong",
        volume_24h=900000,
        liquidity=300000,
        holder_count=4000,
        buys=900,
        sells=100,
        rank=40,
        twitter="https://x.com/s",
    )
    client = FakeClient(rank=[weak, strong])
    result = await GMGNIntelService(client=client).ranked_trending("sol", "1h", 50)
    assert [t["address"] for t in result["tokens"]] == ["strong", "weak"]
    assert result["ordering"] == "our_score_desc"
    assert result["tokens"][0]["trending_rank"] == 40


async def test_token_intel_merges_security_score_and_smart_money() -> None:
    client = FakeClient(
        info={
            "address": "T1",
            "symbol": "AAA",
            "launchpad_status": 1,
            "liquidity": "50000",
            "holder_count": 1200,
            "circulating_supply": "1000000",
            "price": {"price": "0.01", "volume_24h": "250000", "buys_24h": 400, "sells_24h": 200},
            "migrated_timestamp": 1792828027,
            "creation_timestamp": 1792501560,
            "link": {"twitter": "https://x.com/aaa"},
        },
        security={"is_honeypot": False, "rug_ratio": 0.1, "top_10_holder_rate": 0.2},
    )
    service = GMGNIntelService(provider=FakeProvider(), client=client)
    token = await service.token_intel("T1", "sol", True)
    assert token["stage"] == "graduated"
    assert token["security"]["source"] == "GET /v1/token/security"
    assert token["score"] > 0
    assert token["smart_money"]["smart_money_activity"] == [{"wallet": "W"}]
    assert token["market_cap_usd"] == 10000.0


async def test_token_intel_marks_missing_info() -> None:
    service = GMGNIntelService(provider=FakeProvider(), client=FakeClient())
    token = await service.token_intel("T2", "sol", False)
    fields = {item["field"] for item in token["missing"]}
    assert "token_info" in fields
    assert token["security"]["missing"][0]["field"] == "security"


async def test_wallet_intel_delegates_and_normalizes() -> None:
    provider = FakeProvider()
    profile = await GMGNIntelService(provider=provider, client=FakeClient()).wallet_intel("W1", "sol", "7d")
    assert provider.wallet_calls == [("W1", "sol", "7d")]
    assert profile["chain"] == "solana"
    assert profile["holdings"] is None


def test_descriptor_lists_endpoints_and_exclusions() -> None:
    desc = GMGNIntelService(client=FakeClient()).descriptor()
    assert "POST /v1/trenches" in desc["endpoints"]
    assert any("wallet_holdings" in item for item in desc["excluded"])
    assert set(desc["stages"]) == {"new", "bonding", "almost_graduated", "graduated", "trending"}
