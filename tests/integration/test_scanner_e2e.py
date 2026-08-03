"""End-to-end integration tests for the full scanner pipeline.

Pipeline under test: scan request → data layer (ClickHouse own-data + external
API fallbacks) → risk scoring → RAG ingest → Telegram alert → API response.

External dependencies (ClickHouse, Redis, GoPlus/DexScreener HTTP) are mocked;
all internal logic is real:

  - UnifiedTokenScanner phase orchestration + merge methods
  - compute_score() risk scoring
  - DataBus ProviderChain fallback ordering (ClickHouse → GoPlus → DexScreener)
  - deployer fingerprint engine (ClickHouse deployer_events → risk bucket)
  - wallet flow graph engine (evm_transfers → hop expansion → USD graph)
  - publish_rag_finding() payload contract
  - send_scanner_alert() trigger gating (CRITICAL fires, LOW does not)
"""

from __future__ import annotations

import json
import sys
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ── Known test addresses ─────────────────────────────────────────────────────
TOKEN = "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"  # high-risk test token
SAFE_TOKEN = "0x000000000000000000000000000000000000dEaD"
DEPLOYER = "0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddead"
WALLET = "0x1111111111111111111111111111111111111111"
CP1 = "0x2222222222222222222222222222222222222222"
CP2 = "0x3333333333333333333333333333333333333333"
CP3 = "0x4444444444444444444444444444444444444444"


# ═══════════════════════════════════════════════════════════════════════════
# DataBus fixtures — simulate what ClickHouse-backed own-data providers and
# external API providers (GoPlus / DexScreener) return for each chain name.
# ═══════════════════════════════════════════════════════════════════════════


def _critical_databus_responses() -> dict[str, dict[str, Any]]:
    """DataBus responses producing a CRITICAL scan (safety score ~0)."""
    return {
        "token_price": {
            "price_usd": 0.0000012,
            "market_cap_usd": 81234.5,
            "volume_24h": 45000.0,
            "liquidity_usd": 1832.0,
            "source": "dexscreener",
        },
        "token_metadata": {"name": "RuggyToken", "symbol": "RUG", "age_hours": 3.2},
        "token_security": {
            "checks": {
                "honeypot": {"status": "fail"},
                "mintable": {"status": "fail"},
                "proxy_contract": {"status": "fail"},
                "owner_can_modify": {"status": "fail"},
                "transfer_pausable": {"status": "pass"},
            },
            "source": "goplus",
        },
        "holder_data": {"top_10_pct": 92.0},
        "holder_health": {"gini": 0.91},
        "liquidity_risk": {"locked_pct": 5.0},
        "dev_reputation": {"risk_score": 95, "risk_label": "CRITICAL"},
        "rug_patterns": {"match_count": 3},
        "volume_authenticity": {"fake_volume_pct": 82.0},
        "chain_indexer_patterns": {
            "findings": [
                {"pattern": "pump_and_dump", "detected": True, "risk_score": 20},
                {"pattern": "deployer_cluster", "detected": True, "risk_score": 15},
            ]
        },
    }


def _safe_databus_responses() -> dict[str, dict[str, Any]]:
    """DataBus responses producing a SAFE scan (safety score >= 80)."""
    return {
        "token_price": {
            "price_usd": 2.5,
            "market_cap_usd": 50_000_000,
            "volume_24h": 1_200_000,
            "liquidity_usd": 3_400_000,
            "source": "coingecko",
        },
        "token_metadata": {"name": "SafeCoin", "symbol": "SAFE", "age_hours": 24 * 365 * 2},
        "token_security": {
            "checks": {
                "honeypot": {"status": "pass"},
                "mintable": {"status": "pass"},
                "proxy_contract": {"status": "pass"},
                "owner_can_modify": {"status": "pass"},
                "tax_check": {"status": "pass"},
            },
            "source": "goplus",
        },
        "holder_data": {"top_10_pct": 18.0},
        "holder_health": {"gini": 0.4},
        "liquidity_risk": {"locked_pct": 95.0},
        "dev_reputation": {"risk_score": 5, "risk_label": "CLEAN"},
        "rug_patterns": {"match_count": 0},
        "volume_authenticity": {"fake_volume_pct": 4.0},
        "chain_indexer_patterns": {"findings": []},
    }


def _make_fetch_dispatcher(responses: dict[str, dict[str, Any]]) -> Any:
    """Return an async function mimicking DataBus.fetch for the mock DataBus.

    Unknown chain names return None — exactly what a provider chain does when
    every provider fails — so the scanner's graceful-degradation path is real.
    """

    async def _dispatcher(data_type: str, admin_key: str = "", **kwargs: Any) -> dict[str, Any] | None:
        return responses.get(data_type)

    return _dispatcher


@contextmanager
def _scan_externals(
    responses: dict[str, dict[str, Any]],
    *,
    publish_rag: AsyncMock | None = None,
    send_alert: AsyncMock | None = None,
    redis: Any = None,
    keep_event_bus: bool = False,
    keep_publish_rag: bool = False,
):
    """Mock every external dependency the scanner touches; run real internal logic.

    - DataBus.fetch → canned per-chain responses (simulates ClickHouse +
      external API providers at the DataBus boundary).
    - persist_result / auto_label_deployer → fire-and-forget side effects, mocked.
    - Event bus → mocked unless keep_event_bus=True (it touches Redis).
    - publish_rag_finding → mocked unless keep_publish_rag=True (for the
      dedicated RAG payload test).
    """
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.databus.services.client.DataBus.fetch",
                new=AsyncMock(side_effect=_make_fetch_dispatcher(responses)),
            )
        )
        stack.enter_context(patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock()))
        stack.enter_context(patch("app.domains.scanner.unified.scanner.auto_label_deployer", new=AsyncMock()))
        if publish_rag is not None:
            stack.enter_context(patch("app.domains.scanner.unified.scanner.publish_rag_finding", new=publish_rag))
        elif not keep_publish_rag:
            stack.enter_context(patch("app.domains.scanner.unified.scanner.publish_rag_finding", new=AsyncMock()))
        # Always patch send_scanner_alert: a CRITICAL scan would otherwise make
        # real Telegram HTTP calls in tests. Tests that assert on the alert pass
        # their own mock; everyone else gets a silent default.
        stack.enter_context(patch("app.domains.telegram.alert_hooks.send_scanner_alert", new=send_alert or AsyncMock()))
        if redis is not None:
            # return_value (not new): get_redis_async() is a sync function
            # returning a client — the mock must RETURN redis when called.
            stack.enter_context(patch("app.core.redis.get_redis_async", return_value=redis))
        if not keep_event_bus:
            stack.enter_context(patch("app.core.events.get_event_bus", return_value=AsyncMock()))
        yield


# ═══════════════════════════════════════════════════════════════════════════
# Test 1 — full scan pipeline: request → ClickHouse/data → scoring → response
# ═══════════════════════════════════════════════════════════════════════════


class TestFullScanPipeline:
    """Full scan: request → data layer → risk scoring → response."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_full_scan_pipeline_engine_level(self) -> None:
        """UnifiedTokenScanner.scan() returns a fully-populated ScanResult.

        Verifies the response contains all expected fields:
        risk_score (100 - safety), flags, security_checks, market_data.
        """
        from app.domains.scanner.unified import UnifiedTokenScanner

        scanner = UnifiedTokenScanner()
        with _scan_externals(_critical_databus_responses()):
            result = await scanner.scan(address=TOKEN, chain="ethereum", tier="free")

        # ── Risk scoring ──
        assert result.risk_level == "critical", f"expected critical, got {result.risk_level}"
        risk_score = 100 - float(result.safety_score)
        assert risk_score >= 80, f"expected high risk_score, got {risk_score}"
        assert result.safety_score < 20

        # ── Flags ──
        assert "honeypot" in result.risk_flags
        assert "mintable" in result.risk_flags
        assert "DEV_HIGH_RISK" in result.risk_flags
        assert "LP_LOCK_LOW" in result.risk_flags
        assert any(f.startswith("RUG_PATTERN_MATCH") for f in result.risk_flags)
        assert any(f.startswith("ONCHAIN_") for f in result.risk_flags)

        # ── Security checks merged from token_security chain ──
        assert result.security is not None
        checks = result.security.get("checks", {})
        assert checks.get("honeypot", {}).get("status") == "fail"
        assert checks.get("transfer_pausable", {}).get("status") == "pass"
        assert result.security.get("source") == "goplus"

        # ── Market data merged from token_price chain ──
        assert result.price_usd == pytest.approx(0.0000012)
        assert result.market_cap_usd == pytest.approx(81234.5)
        assert result.volume_24h_usd == pytest.approx(45000.0)
        assert result.liquidity_usd == pytest.approx(1832.0)

        # ── Identity + deep enrichment ──
        assert result.name == "RuggyToken"
        assert result.symbol == "RUG"
        assert result.holders == {"top_10_pct": 92.0}
        assert result.dev_reputation == {"risk_score": 95, "risk_label": "CRITICAL"}
        assert result.chain_indexer_patterns is not None
        assert "token_price" in result.modules_run
        assert result.scan_duration_ms >= 0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_full_scan_pipeline_api_response(self) -> None:
        """POST /api/v2/scanner/token/scan returns the mapped API response.

        The v2 endpoint maps ScanResult → {status, safety_score, risk_level,
        risk_flags, price_usd, ...}; risk_score == 100 - safety_score.
        """
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.routers.unified_scanner_router import router

        app = FastAPI()
        app.include_router(router)
        transport = ASGITransport(app=app)

        with _scan_externals(_critical_databus_responses()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v2/scanner/token/scan",
                    json={"address": TOKEN, "chain": "ethereum"},
                )

        assert resp.status_code == 200, f"got {resp.status_code}: {resp.text[:300]}"
        body = resp.json()
        assert body["status"] == "ok"
        assert body["token"] == TOKEN.lower()
        assert body["chain"] == "ethereum"
        assert body["name"] == "RuggyToken"
        assert body["symbol"] == "RUG"
        # risk_score == 100 - safety_score
        assert 100 - float(body["safety_score"]) >= 80
        assert body["risk_level"] == "critical"
        assert "honeypot" in body["risk_flags"]
        assert body["price_usd"] == pytest.approx(0.0000012)
        assert body["data_source"] == "databus"
        assert "token_security" in body["modules_run"]


# ═══════════════════════════════════════════════════════════════════════════
# Test 2 — scan → RAG ingest
# ═══════════════════════════════════════════════════════════════════════════


class TestScanToRagIngest:
    """After a scan, publish_rag_finding is called with the correct structure."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_scan_calls_publish_rag_finding_with_scan_result(self) -> None:
        """A critical scan invokes publish_rag_finding with the ScanResult."""
        from app.domains.scanner.unified import UnifiedTokenScanner
        from app.domains.scanner.unified.model import ScanResult

        publish_rag = AsyncMock()
        scanner = UnifiedTokenScanner()
        with _scan_externals(_critical_databus_responses(), publish_rag=publish_rag):
            result = await scanner.scan(address=TOKEN, chain="ethereum", tier="free")

        publish_rag.assert_awaited_once()
        arg = publish_rag.await_args.args[0]
        assert isinstance(arg, ScanResult)
        assert arg.token_address == TOKEN.lower()
        assert arg.chain == "ethereum"
        assert arg.risk_level == "critical"
        assert arg.safety_score == result.safety_score
        assert arg.risk_flags  # non-empty for a critical scan

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_scan_does_not_publish_rag_for_safe_token(self) -> None:
        """RAG ingest only fires for high/critical risk scans."""
        from app.domains.scanner.unified import UnifiedTokenScanner

        publish_rag = AsyncMock()
        scanner = UnifiedTokenScanner()
        with _scan_externals(_safe_databus_responses(), publish_rag=publish_rag):
            result = await scanner.scan(address=SAFE_TOKEN, chain="ethereum", tier="free")

        assert result.risk_level in ("safe", "low")
        publish_rag.assert_not_awaited()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_publish_rag_finding_payload_structure(self) -> None:
        """Real publish_rag_finding publishes the exact RAG payload contract.

        Redis channel ``rmi:rag:ingest`` + queue ``rmi:rag:findings_queue``
        receive a JSON payload with the documented finding fields.
        """
        from app.domains.scanner.unified import UnifiedTokenScanner

        redis = AsyncMock()
        redis.publish = AsyncMock()
        redis.lpush = AsyncMock()
        # Dedup gate in publish_rag_finding: exists() must be falsy so the
        # finding is published, and setex() records the 24h dedup key.
        redis.exists.return_value = False
        redis.setex = AsyncMock()

        scanner = UnifiedTokenScanner()
        with _scan_externals(_critical_databus_responses(), redis=redis, keep_publish_rag=True):
            await scanner.scan(address=TOKEN, chain="ethereum", tier="free")

        redis.publish.assert_awaited_once()
        redis.lpush.assert_awaited_once()
        channel, payload_json = redis.publish.await_args.args
        assert channel == "rmi:rag:ingest"
        assert redis.lpush.await_args.args[0] == "rmi:rag:findings_queue"

        payload = json.loads(payload_json)
        expected_fields = {
            "token_address",
            "chain",
            "name",
            "symbol",
            "safety_score",
            "confidence",
            "risk_level",
            "risk_flags",
            "green_flags",
            "price_usd",
            "liquidity_usd",
            "age_hours",
            "scanned_at",
            "modules_run",
        }
        assert expected_fields.issubset(payload.keys()), f"missing fields: {expected_fields - payload.keys()}"
        assert payload["token_address"] == TOKEN.lower()
        assert payload["chain"] == "ethereum"
        assert payload["risk_level"] == "critical"
        assert "honeypot" in payload["risk_flags"]
        assert payload["modules_run"]  # non-empty module list


# ═══════════════════════════════════════════════════════════════════════════
# Test 3 — scan → Telegram alert
# ═══════════════════════════════════════════════════════════════════════════


class TestScanToTelegramAlert:
    """CRITICAL scans trigger send_scanner_alert; LOW scans do not."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_critical_scan_triggers_scanner_alert(self) -> None:
        """A critical scan calls send_scanner_alert with the correct payload."""
        from app.domains.scanner.unified import UnifiedTokenScanner

        send_alert = AsyncMock()
        scanner = UnifiedTokenScanner()
        # Real should_send_scanner_alert() gating: critical → True
        with _scan_externals(_critical_databus_responses(), send_alert=send_alert):
            result = await scanner.scan(address=TOKEN, chain="ethereum", tier="free")

        assert result.risk_level == "critical"
        send_alert.assert_awaited_once()
        kwargs = send_alert.await_args.kwargs
        assert kwargs["token_address"] == TOKEN.lower()
        assert kwargs["chain"] == "ethereum"
        assert kwargs["risk_score"] == pytest.approx(100 - float(result.safety_score))
        assert kwargs["token_name"] == "RuggyToken"
        assert kwargs["token_symbol"] == "RUG"
        assert "honeypot" in kwargs["threats"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_low_scan_does_not_trigger_scanner_alert(self) -> None:
        """A low-risk scan does not call send_scanner_alert.

        Real should_send_scanner_alert() gating: safe risk_level + no severe
        red flags → False → alert skipped.
        """
        from app.domains.scanner.unified import UnifiedTokenScanner

        send_alert = AsyncMock()
        scanner = UnifiedTokenScanner()
        with _scan_externals(_safe_databus_responses(), send_alert=send_alert):
            result = await scanner.scan(address=SAFE_TOKEN, chain="ethereum", tier="free")

        assert result.risk_level in ("safe", "low")
        send_alert.assert_not_awaited()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_send_scanner_alert_threshold_skips_low_risk(self) -> None:
        """Direct send_scanner_alert() skips when risk < 60 and no severe flag."""
        from app.domains.telegram.alert_hooks import send_scanner_alert

        with patch("app.domains.telegram.alert_hooks.send_alert", new=AsyncMock()) as send_alert:
            out = await send_scanner_alert(
                token_address=TOKEN,
                chain="ethereum",
                risk_score=20,
                threats=["routine_activity_only"],
                token_name="SafeCoin",
                token_symbol="SAFE",
            )

        assert out["ok"] is True
        assert out["skipped"] == "risk_below_threshold"
        send_alert.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# Test 4 — DataBus fallback chain (ClickHouse → GoPlus → DexScreener)
# ═══════════════════════════════════════════════════════════════════════════


class TestDataBusFallbackChain:
    """When ClickHouse returns no data, the fallback chain is tried in order."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_fallback_tries_clickhouse_then_goplus_then_dexscreener(self) -> None:
        """All providers fail except DexScreener → it wins and order is preserved."""
        from app.domains.databus.providers.base import Provider, ProviderChain, ProviderTier

        calls: list[str] = []

        async def clickhouse(**kwargs: Any) -> None:
            calls.append("clickhouse")
            return None  # own data empty

        async def goplus(**kwargs: Any) -> None:
            calls.append("goplus")
            return None  # API fails / no data

        async def dexscreener(**kwargs: Any) -> dict[str, Any]:
            calls.append("dexscreener")
            return {
                "check": "dexscreener",
                "result": "scanned",
                "risk_score": 30,
                "is_honeypot": False,
                "liquidity_usd": 500.0,
                "source": "dexscreener_heuristics",
                "tier": "free_api",
            }

        chain = ProviderChain(
            "token_security_free",
            providers=[
                Provider("free_security_clickhouse", ProviderTier.LOCAL, clickhouse, weight=20.0, is_local=True),
                Provider("free_security_goplus", ProviderTier.FREE_API, goplus, weight=10.0),
                Provider("free_security_dexscreener", ProviderTier.FREE_API, dexscreener, weight=4.0),
            ],
        )

        result = await chain.fetch(token_address=TOKEN, chain="ethereum")

        assert calls == ["clickhouse", "goplus", "dexscreener"]
        assert result is not None
        assert result["source"] == "dexscreener_heuristics"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_clickhouse_success_skips_external_fallbacks(self) -> None:
        """When ClickHouse own-data answers, GoPlus/DexScreener are never called."""
        from app.domains.databus.providers.base import Provider, ProviderChain, ProviderTier

        calls: list[str] = []

        async def clickhouse(**kwargs: Any) -> dict[str, Any]:
            calls.append("clickhouse")
            return {
                "check": "goplus",
                "result": "scanned",
                "risk_score": 5,
                "is_honeypot": False,
                "source": "own_clickhouse",
                "tier": "local",
                "is_local": True,
            }

        async def goplus(**kwargs: Any) -> dict[str, Any]:
            calls.append("goplus")
            return {"check": "goplus", "result": "scanned", "source": "goplus"}

        async def dexscreener(**kwargs: Any) -> dict[str, Any]:
            calls.append("dexscreener")
            return {"check": "dexscreener", "result": "scanned", "source": "dexscreener_heuristics"}

        chain = ProviderChain(
            "token_security_free",
            providers=[
                Provider("free_security_clickhouse", ProviderTier.LOCAL, clickhouse, weight=20.0, is_local=True),
                Provider("free_security_goplus", ProviderTier.FREE_API, goplus, weight=10.0),
                Provider("free_security_dexscreener", ProviderTier.FREE_API, dexscreener, weight=4.0),
            ],
        )

        result = await chain.fetch(token_address=TOKEN, chain="ethereum")

        assert calls == ["clickhouse"]
        assert result["source"] == "own_clickhouse"

    @pytest.mark.integration
    def test_real_token_security_free_chain_ordering(self) -> None:
        """The real token_security_free chain is wired ClickHouse-first, DexScreener-last."""
        from app.domains.databus.providers.registry import build_provider_chains

        chains = build_provider_chains()
        chain = chains["token_security_free"]
        names = [p.name for p in chain.providers]

        assert "free_security_clickhouse" in names
        assert "free_security_goplus" in names
        assert "free_security_dexscreener" in names
        # Own data must be first; external APIs are fallbacks.
        assert names.index("free_security_clickhouse") < names.index("free_security_goplus")
        assert names.index("free_security_goplus") < names.index("free_security_dexscreener")


# ═══════════════════════════════════════════════════════════════════════════
# Test 5 — deployer fingerprint pipeline
# ═══════════════════════════════════════════════════════════════════════════


class FakeDeployerCHClient:
    """Sync ClickHouse stand-in dispatching on fingerprint SQL fragments."""

    def __init__(
        self,
        stats: list[tuple] | None = None,
        tokens: list[tuple] | None = None,
        dex: list[tuple] | None = None,
        funder_native: list[tuple] | None = None,
        funder_labels: list[tuple] | None = None,
        funder_tx_count: list[tuple] | None = None,
        same_funder_deployers: list[tuple] | None = None,
        same_funder_overlap: list[tuple] | None = None,
    ) -> None:
        self.stats = stats or []
        self.tokens = tokens or []
        self.dex = dex or []
        self.funder_native = funder_native
        self.funder_labels = funder_labels or []
        self.funder_tx_count = funder_tx_count or [(0,)]
        self.same_funder_deployers = same_funder_deployers or []
        self.same_funder_overlap = same_funder_overlap or [(0,)]
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if "count() AS total_deployments" in sql:
            return self.stats
        if "first_dex_ts" in sql:
            return self.dex
        if "GROUP BY token_address" in sql:
            return self.tokens
        if "lower(token_address) IN" in sql:
            return self.funder_native or []
        if "wallet_memory.wallet_labels" in sql and "WHERE address = %(addr)s" in sql:
            return self.funder_labels
        if "LIMIT 11" in sql:
            return self.funder_tx_count
        if "SELECT DISTINCT deployer" in sql:
            return self.same_funder_deployers
        if "count(DISTINCT to_address)" in sql:
            return self.same_funder_overlap
        return []


def _naive_dt(days_ago: float = 0.0) -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)


class TestDeployerFingerprintPipeline:
    """Scan a new token → deployer events queried → fingerprint → scan result."""

    def setup_method(self) -> None:
        from app.domains.scanner.engines.deployer_fingerprint import clear_cache

        clear_cache()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_deployer_events_queried_and_fingerprint_computed(self) -> None:
        """fingerprint_deployer() queries ClickHouse deployer_events and buckets a NEW deployer."""
        from app.domains.scanner.engines.deployer_fingerprint import fingerprint_deployer

        deployed = _naive_dt(days_ago=2)
        fake = FakeDeployerCHClient(
            stats=[(1, 1, ["ethereum"], deployed, deployed)],
            tokens=[("0xaaa1", "ethereum", deployed, "0xdeploy")],
        )
        with (
            patch("app.domains.scanner.engines.deployer_fingerprint._get_ch_client", return_value=fake),
            patch("app.domains.scanner.engines.deployer_fingerprint._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.deployer_fingerprint._redis_set", AsyncMock(return_value=True)),
        ):
            out = await fingerprint_deployer(DEPLOYER, "ethereum")

        assert out["result"] == "scanned"
        assert out["risk_bucket"] == "NEW"
        assert out["total_deployments"] == 1
        assert out["risk_score"] == 50
        assert any("deployer_events" in sql for sql in fake.executed), "deployer_events was not queried"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_databus_provider_wraps_fingerprint(self) -> None:
        """The DataBus provider returns the fingerprint wrapped with source metadata."""
        from app.domains.databus.providers.chains.chain_indexer_own import _chain_indexer_deployer_fingerprint

        deployed = _naive_dt(days_ago=2)
        fake = FakeDeployerCHClient(
            stats=[(1, 1, ["ethereum"], deployed, deployed)],
            tokens=[("0xaaa1", "ethereum", deployed, "0xdeploy")],
        )
        with (
            patch("app.domains.scanner.engines.deployer_fingerprint._get_ch_client", return_value=fake),
            patch("app.domains.scanner.engines.deployer_fingerprint._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.deployer_fingerprint._redis_set", AsyncMock(return_value=True)),
        ):
            out = await _chain_indexer_deployer_fingerprint(deployer=DEPLOYER, chain="ethereum")

        assert out is not None
        assert out["is_local"] is True
        assert "ClickHouse deployer behavioral fingerprint" in out["source"]
        assert out["data"]["result"] == "scanned"
        assert out["data"]["risk_bucket"] == "NEW"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_fingerprint_flows_into_scan_result(self) -> None:
        """A high-risk deployer fingerprint in dev_reputation affects the scan.

        The scanner's DEEP phase fetches dev_reputation from DataBus; when that
        returns fingerprint-shaped data, it lands in ScanResult.dev_reputation
        and compute_score() raises DEV_HIGH_RISK.
        """
        from app.domains.scanner.unified import UnifiedTokenScanner

        responses = {
            "token_price": {
                "price_usd": 0.01,
                "market_cap_usd": 5000,
                "volume_24h": 100,
                "liquidity_usd": 900,
                "source": "dexscreener",
            },
            "token_metadata": {"name": "NewToken", "symbol": "NEWT", "age_hours": 1.0},
            "token_security": {
                "checks": {"honeypot": {"status": "pass"}, "mintable": {"status": "pass"}},
                "source": "goplus",
            },
            # dev_reputation returns a deployer fingerprint (risk_score 90)
            "dev_reputation": {
                "deployer_address": DEPLOYER,
                "risk_score": 90,
                "risk_label": "REPEAT_RUG",
                "risk_bucket": "REPEAT_RUG",
                "total_deployments": 4,
                "success_rate": 0.25,
                "source": "own_clickhouse",
            },
        }

        scanner = UnifiedTokenScanner()
        with _scan_externals(responses):
            result = await scanner.scan(address=TOKEN, chain="ethereum", tier="free")

        assert result.dev_reputation is not None
        assert result.dev_reputation["deployer_address"] == DEPLOYER
        assert result.dev_reputation["risk_score"] == 90
        assert "DEV_HIGH_RISK" in result.risk_flags
        assert result.risk_level in ("high", "critical")


# ═══════════════════════════════════════════════════════════════════════════
# Test 6 — wallet flow pipeline
# ═══════════════════════════════════════════════════════════════════════════


class FakeFlowCHClient:
    """Sync ClickHouse stand-in dispatching on wallet_flow SQL fragments."""

    def __init__(
        self,
        hop1: list[tuple] | None = None,
        hop2: list[tuple] | None = None,
        labels: list[tuple] | None = None,
        contracts: list[tuple] | None = None,
    ) -> None:
        self.hop1 = hop1 or []
        self.hop2 = hop2 or []
        self.labels = labels or []
        self.contracts = contracts or []
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if "wallet_memory.wallet_labels" in sql and "address IN" in sql:
            return self.labels
        if "wallet_memory.dex_events" in sql and "contract_address IN" in sql:
            return self.contracts
        if "from_address = %(wallet)s OR to_address = %(wallet)s" in sql:
            return self.hop1
        if "from_address = %(cp)s OR to_address = %(cp)s" in sql:
            return self.hop2
        return []


@contextmanager
def _mock_flow_externals(fake: FakeFlowCHClient, usd_value: float | None = None):
    """Mock ClickHouse, Redis, and the USD oracle for build_flow_graph."""
    with ExitStack() as stack:
        stack.enter_context(patch("app.domains.scanner.engines.wallet_flow._get_ch_client", return_value=fake))
        stack.enter_context(patch("app.domains.scanner.engines._common._get_ch_client", return_value=fake))
        stack.enter_context(patch("app.domains.scanner.engines.wallet_flow._redis_get", AsyncMock(return_value=None)))
        stack.enter_context(patch("app.domains.scanner.engines.wallet_flow._redis_set", AsyncMock(return_value=True)))
        if usd_value is not None:
            stack.enter_context(
                patch(
                    "app.domains.scanner.engines.wallet_flow._normalize_token_usd",
                    AsyncMock(return_value=usd_value),
                )
            )
        yield


class TestWalletFlowPipeline:
    """Scan a wallet → evm_transfers queried → hops expanded → USD graph."""

    def setup_method(self) -> None:
        from app.domains.scanner.engines.wallet_flow import clear_cache

        clear_cache()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_wallet_flow_queries_transfers_builds_graph(self) -> None:
        """build_flow_graph() queries evm_transfers and returns a USD graph."""
        from app.domains.scanner.engines.wallet_flow import build_flow_graph

        fake = FakeFlowCHClient(
            hop1=[
                (WALLET, CP1, "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 4000, "0xaaa"),  # outflow
                (CP2, WALLET, "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 1000, "0xbbb"),  # inflow
            ],
            hop2=[(CP1, CP3, "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 300, "0xccc")],
            labels=[
                (CP1, "Uniswap V3 Router", "dex", "router", "eth_labels", 0),
            ],
            contracts=[(CP3,)],
        )
        with _mock_flow_externals(fake, usd_value=5000.0):
            out = await build_flow_graph(WALLET, "ethereum", hops=2)

        assert out["result"] == "scanned"
        # ── evm_transfers were queried ──
        assert any("evm_transfers" in sql for sql in fake.executed), "evm_transfers was not queried"
        # ── Graph built ──
        assert out["node_count"] >= 3
        assert out["edge_count"] >= 2
        node_ids = {n["id"] for n in out["nodes"]}
        assert WALLET in node_ids
        assert CP1 in node_ids  # hop-1 counterparty
        assert CP3 in node_ids  # hop-2 neighbor expanded
        # ── USD normalization applied (proportional allocation) ──
        # Token raw total includes the hop-2 edge (300), so per-edge USD is a
        # proportional share. The ratio between the two hop-1 edges must
        # equal the raw transfer ratio (4000 : 1000).
        edge_usd = {(e["from"], e["to"]): e["usd"] for e in out["edges"]}
        assert edge_usd[(WALLET, CP1)] > 0
        assert edge_usd[(CP2, WALLET)] > 0
        assert edge_usd[(WALLET, CP1)] / edge_usd[(CP2, WALLET)] == pytest.approx(4.0, rel=1e-3)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_databus_provider_wraps_wallet_flow(self) -> None:
        """The DataBus provider wraps build_flow_graph with source metadata."""
        from app.domains.databus.providers.chains.chain_indexer_own import _chain_indexer_wallet_flow

        fake = FakeFlowCHClient(
            hop1=[
                (WALLET, CP1, "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 1000, "0xaaa"),
                (CP2, WALLET, "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 2000, "0xbbb"),
            ]
        )
        with _mock_flow_externals(fake, usd_value=3000.0):
            out = await _chain_indexer_wallet_flow(wallet=WALLET, chain="ethereum", hops=1)

        assert out is not None
        assert out["is_local"] is True
        assert "ClickHouse evm_transfers wallet flow" in out["source"]
        assert out["data"]["result"] == "scanned"
        assert out["data"]["edge_count"] >= 2


# ═══════════════════════════════════════════════════════════════════════════
# Smoke guard — scanner classes remain importable and wiring is intact
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineWiring:
    """Static wiring checks — the pipeline pieces all exist and connect."""

    @pytest.mark.integration
    def test_scanner_phase_chains_defined(self) -> None:
        """UnifiedTokenScanner defines the 4 dependency-ordered phase chains."""
        from app.domains.scanner.unified import UnifiedTokenScanner

        assert "token_security" in UnifiedTokenScanner.CORE_CHAINS
        assert "dev_reputation" in UnifiedTokenScanner.DEEP_CHAINS
        assert "chain_indexer_patterns" in UnifiedTokenScanner.DEEP_CHAINS
        assert "smart_money" in UnifiedTokenScanner.INTEL_CHAINS

    @pytest.mark.integration
    def test_alert_and_rag_symbols_exist(self) -> None:
        """The RAG publisher and Telegram alert hooks are importable."""
        from app.domains.scanner.unified.persistence import publish_rag_finding
        from app.domains.telegram.alert_hooks import send_scanner_alert, should_send_scanner_alert

        assert callable(publish_rag_finding)
        assert callable(send_scanner_alert)
        assert callable(should_send_scanner_alert)
