"""Error-path tests for the legacy scanner (``tools/scanner.py``).

Covers the real-world provider failure modes that must never crash a scan
reply: ``None``/garbage numeric fields, malformed DexScreener pair payloads,
non-dict v2 evidence blocks, cache staleness/deep-copy isolation, and
providers that raise or return garbage JSON. All tests are offline —
provider HTTP calls are faked at the ``httpx.AsyncClient`` boundary.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx

from app.domains.telegram.rugmunchbot.tools import scanner

if TYPE_CHECKING:
    import pytest


# ── Null-safe numeric coercion ─────────────────────────────────────────────


class TestNullSafeNumbers:
    def test_safe_float_none_defaults_zero(self) -> None:
        assert scanner._safe_float(None) == 0.0

    def test_safe_float_garbage_defaults_zero(self) -> None:
        assert scanner._safe_float("garbage") == 0.0
        assert scanner._safe_float([1, 2]) == 0.0

    def test_safe_float_respects_custom_default(self) -> None:
        assert scanner._safe_float(None, default=5.0) == 5.0

    def test_safe_float_preserves_valid_numbers(self) -> None:
        assert scanner._safe_float("1.5") == 1.5
        assert scanner._safe_float(0) == 0.0

    def test_ds_timeframe_map_non_dict_returns_empty(self) -> None:
        assert scanner._ds_timeframe_map(None, ("h1",)) == {}
        assert scanner._ds_timeframe_map("bad", ("h1",)) == {}

    def test_ds_timeframe_map_garbage_value_is_none_not_zero(self) -> None:
        """Keep-it-honest: an unparseable timeframe renders None, never 0."""
        out = scanner._ds_timeframe_map({"h1": "garbage", "h24": None}, ("h1", "h24"))
        assert out == {"h1": None, "h24": None}


# ── DexScreener market snapshot ────────────────────────────────────────────


class TestDexScreenerSnapshot:
    def test_market_snapshot_non_list_returns_empty(self) -> None:
        assert scanner._ds_market_snapshot(None) == {}
        assert scanner._ds_market_snapshot("not-a-list") == {}

    def test_market_snapshot_skips_non_dict_pairs(self) -> None:
        """A pair list containing garbage entries must not crash the pick."""
        snap = scanner._ds_market_snapshot(["bad", None, {"liquidity": None}])
        assert snap["liquidity"] is None
        assert snap["price"] is None
        assert snap["symbol"] == ""

    def test_market_snapshot_null_liquidity_is_none_not_zero(self) -> None:
        snap = scanner._ds_market_snapshot([{"liquidity": {"usd": None}}])
        assert snap["liquidity"] is None

    def test_merge_market_snapshot_noops_on_empty(self) -> None:
        result: dict[str, Any] = {"mcap": None, "liquidity": None}
        scanner._merge_market_snapshot(result, None)
        scanner._merge_market_snapshot(result, "bad")
        scanner._merge_market_snapshot(result, {})
        assert result == {"mcap": None, "liquidity": None}


# ── v2 payload flattening with missing/garbage blocks ──────────────────────


class TestFlattenV2Malformed:
    def test_flatten_v2_null_evidence_and_sentinel(self) -> None:
        """Providers can omit evidence/sentinel entirely — flatten must not
        crash and must keep honest None fields."""
        flat = scanner._flatten_v2({"status": "ok", "evidence": None, "sentinel": None}, "0xabc", "ethereum")
        assert flat["status"] == "ok"
        assert flat["honeypot"] == "unknown"
        assert flat["security_checks"] == {}
        assert flat["bundle_analysis"] == {
            "result": None,
            "is_bundled": None,
            "risk_label": None,
            "confidence": None,
            "launch_cluster_score": None,
            "common_funder_score": None,
            "similar_amount_score": None,
            "deployer_funded_pct": None,
            "first_time_buyer_pct": None,
            "launch_buyers": None,
            "cluster_blocks": None,
            "common_funders": None,
        }

    def test_flatten_v2_garbage_sub_blocks(self) -> None:
        flat = scanner._flatten_v2(
            {
                "status": "ok",
                "evidence": "garbage",
                "sentinel": ["also", "bad"],
                "bundle_analysis": "not-a-dict",
                "socials": ["not-a-dict"],
            },
            "0xabc",
            "solana",
        )
        assert flat["bundle_analysis"]["is_bundled"] is None
        assert flat["socials"] == {}

    def test_v2_token_to_legacy_empty_payload(self) -> None:
        legacy = scanner.v2_token_to_legacy({})
        assert legacy["risk_score"] is not None  # computed from missing safety
        assert legacy["mcap"] is None
        assert legacy["liquidity"] is None
        assert legacy["price"] is None


# ── Scan cache: TTL + deep-copy isolation ──────────────────────────────────


class TestScanCache:
    def test_cache_key_normalizes_chain_and_address(self) -> None:
        assert scanner._scan_cache_key(" ABC ", " SOLANA ") == "solana:abc"

    def test_cache_get_returns_deep_copy(self) -> None:
        scanner._scan_cache.clear()
        scanner._scan_cache_set("0xabc", "ethereum", {"risk_score": 1, "threats": ["a"]})
        hit = scanner._scan_cache_get("0xabc", "ethereum")
        assert hit == {"risk_score": 1, "threats": ["a"]}
        # Mutating the returned copy must not poison the cache entry.
        hit["threats"].append("mutated")
        assert scanner._scan_cache_get("0xabc", "ethereum") == {"risk_score": 1, "threats": ["a"]}

    def test_cache_get_expired_entry_returns_none(self) -> None:
        scanner._scan_cache.clear()
        scanner._scan_cache["solana:dead"] = (time.monotonic() - scanner._SCAN_CACHE_TTL - 10, {"risk_score": 5})
        assert scanner._scan_cache_get("dead", "solana") is None
        assert "solana:dead" not in scanner._scan_cache  # expired entry pruned

    def test_cache_miss_returns_none(self) -> None:
        scanner._scan_cache.clear()
        assert scanner._scan_cache_get("0xnever", "ethereum") is None


# ── Provider failures (fail-open into result["errors"]) ────────────────────


EVM_ADDR = "0x" + "a" * 40


class _FakeProviderClient:
    """httpx.AsyncClient fake: either raises or returns a canned JSON body."""

    def __init__(self, *, kind: str = "ok") -> None:
        self.kind = kind
        self.addr = EVM_ADDR

    async def get(self, url: str, timeout: float | None = None, headers: dict | None = None) -> Any:
        if self.kind == "raise":
            raise httpx.ConnectError("provider down")
        payload = self._body_for(url)

        class _Resp:
            status_code = 200

            def json(self) -> Any:
                return payload

        return _Resp()

    def _body_for(self, url: str) -> Any:
        if "goplus" in url:
            # GoPlus returns the address key only when it knows the token.
            return {"result": {self.addr: {"is_honeypot": "1", "buy_tax": "bad-token"}}}
        if "honeypot" in url:
            return {"isHoneypot": False}
        if "/databus/token/" in url:
            return ["garbage", "not-a-dict"]  # RMI provider returns non-dict
        return {}


def _base_result() -> dict[str, Any]:
    return {
        "chain": "ethereum",
        "mcap": 0,
        "honeypot": None,
        "contract_verified": None,
        "buy_tax": None,
        "sell_tax": None,
        "mintable": None,
        "can_blacklist": None,
        "owner_renounced": None,
        "lp_locked": None,
        "top_holders": [],
        "threats": [],
        "risk_score": 0,
        "errors": [],
    }


class TestProviderFailures:
    async def test_evm_security_provider_network_error_appends_error(self) -> None:
        result = _base_result()
        await scanner._scan_evm_security_provider(_FakeProviderClient(kind="raise"), EVM_ADDR, result)
        assert any("GoPlus" in e for e in result["errors"])
        assert result["honeypot"] is None  # unchanged, honest

    async def test_evm_security_provider_garbage_tax_never_crashes(self) -> None:
        """GoPlus returning a non-numeric buy_tax is coerced via _safe_float."""
        result = _base_result()
        await scanner._scan_evm_security_provider(_FakeProviderClient(kind="ok"), EVM_ADDR, result)
        assert result["buy_tax"] == 0.0
        assert result["honeypot"] is True  # is_honeypot == "1"

    async def test_rmi_provider_network_error_appends_error(self) -> None:
        result = _base_result()
        await scanner._scan_rmi_provider(_FakeProviderClient(kind="raise"), EVM_ADDR, result)
        assert any("RMI" in e for e in result["errors"])

    async def test_scan_token_survives_garbage_provider_payloads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The full scan returns a result when providers return garbage:
        DexScreener empty, GoPlus valid-but-thin, RMI a non-dict body.
        A malformed provider payload never crashes the scan reply."""

        async def _enrich_identity(result: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
            return result

        monkeypatch.setattr(scanner, "enrich_scan_with_gmgn", _enrich_identity)
        monkeypatch.setattr(scanner, "enrich_scan", _enrich_identity)
        monkeypatch.setattr(scanner, "should_index_scan", lambda result, source="": False)
        monkeypatch.setattr(scanner, "schedule_scan_memory_index", lambda result, source="": None)
        monkeypatch.setattr(scanner, "get_shared_client", lambda timeout: _FakeProviderClient(kind="ok"))

        result = await scanner.scan_token(EVM_ADDR, "ethereum")
        assert isinstance(result, dict)
        # Honest fields survive: no market data was reported, so None.
        assert result["mcap"] is None
        assert result["liquidity"] is None
        assert result["threats"]  # GoPlus honeypot + unverified contract surfaced

    async def test_scan_token_survives_all_providers_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every provider raising still yields a scan result with errors —
        never an exception out of scan_token."""

        async def _enrich_identity(result: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
            return result

        monkeypatch.setattr(scanner, "enrich_scan_with_gmgn", _enrich_identity)
        monkeypatch.setattr(scanner, "enrich_scan", _enrich_identity)
        monkeypatch.setattr(scanner, "should_index_scan", lambda result, source="": False)
        monkeypatch.setattr(scanner, "schedule_scan_memory_index", lambda result, source="": None)
        monkeypatch.setattr(scanner, "get_shared_client", lambda timeout: _FakeProviderClient(kind="raise"))

        result = await scanner.scan_token(EVM_ADDR, "ethereum")
        assert isinstance(result, dict)
        assert any("DexScreener" in e for e in result["errors"])
        assert any("GoPlus" in e for e in result["errors"])
        assert any("RMI" in e for e in result["errors"])
        assert result["mcap"] is None
