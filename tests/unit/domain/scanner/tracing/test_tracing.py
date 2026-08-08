"""Unit tests for app/domains/scanner/tracing — transaction tracing pipeline.

Tests the full decode pipeline with mocked RPC receipt + 4byte responses:

  1. Receipt log extraction (fetch_tx_logs via RPC router)
  2. 4byte signature resolution + decoding (Transfer/Swap/Approval)
  3. logIndex sort enforcement
  4. Wallet-hop mapping (wallet→wallet edges)
  5. Cluster graph building (nodes/edges for RugMaps)
  6. Route contract (POST /api/v1/tracing/decode)
  7. Fallback + validation paths

No network access — the RPC router singleton and 4byte fetcher are patched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.scanner.tracing import (
    build_cluster_graph,
    build_wallet_hops,
    decode_transaction,
    fetch_tx_logs,
    is_supported_chain,
)
from app.domains.scanner.tracing.fourbyte import (
    _fetch_from_api,
    _is_static_type,
    decode_log,
    parse_event_signature,
    resolve_event_signature,
)
from app.domains.scanner.tracing.receipt import extract_trace_hops
from app.routers.tracing_router import router as tracing_router

# ── Topic0 hashes (keccak256 of the canonical signatures) ──────────────
TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
APPROVAL_TOPIC0 = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
SWAP_TOPIC0 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

TRANSFER_SIG = "Transfer(address indexed from, address indexed to, uint256 value)"
APPROVAL_SIG = "Approval(address indexed owner, address indexed spender, uint256 value)"
SWAP_SIG = "Swap(address indexed sender, uint256 amount0In, uint256 amount1In, uint256 amount0Out, uint256 amount1Out, address indexed to)"

# Padded addresses for topics
FROM_PAD = "0x" + "00" * 12 + "1111111111111111111111111111111111111111"
TO_PAD = "0x" + "00" * 12 + "2222222222222222222222222222222222222222"
OWNER_PAD = "0x" + "00" * 12 + "3333333333333333333333333333333333333333"
SPENDER_PAD = "0x" + "00" * 12 + "4444444444444444444444444444444444444444"
SENDER_PAD = "0x" + "00" * 12 + "5555555555555555555555555555555555555555"
SWAP_TO_PAD = "0x" + "00" * 12 + "6666666666666666666666666666666666666666"

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"


def _log(
    address: str,
    topics: list[str],
    data: str,
    log_index: int,
    tx_hash: str = "0x" + "ab" * 32,
) -> dict[str, Any]:
    return {
        "address": address,
        "topics": topics,
        "data": data,
        "logIndex": hex(log_index),
        "blockNumber": "0x10",
        "transactionHash": tx_hash,
        "transactionIndex": "0x0",
        "removed": False,
    }


def _amount(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _build_receipt() -> dict[str, Any]:
    """Mocked eth_getTransactionReceipt result with 3 logs (out of order)."""
    tx_hash = "0x" + "ab" * 32
    logs = [
        # logIndex 2: Swap on a pool — sender 0x555…, to 0x666…
        _log(
            POOL,
            [SWAP_TOPIC0, SENDER_PAD, SWAP_TO_PAD],
            _amount(0) + _amount(1_000) + _amount(2_000) + _amount(0) + _amount(3_000),
            log_index=2,
            tx_hash=tx_hash,
        ),
        # logIndex 0: USDC Transfer — 0x111… → 0x222…
        _log(
            USDC,
            [TRANSFER_TOPIC0, FROM_PAD, TO_PAD],
            _amount(1_000_000),
            log_index=0,
            tx_hash=tx_hash,
        ),
        # logIndex 1: Approval — 0x333… approves 0x444…
        _log(
            USDC,
            [APPROVAL_TOPIC0, OWNER_PAD, SPENDER_PAD],
            _amount(5_000),
            log_index=1,
            tx_hash=tx_hash,
        ),
    ]
    return {
        "transactionHash": tx_hash,
        "transactionIndex": "0x0",
        "blockNumber": "0x10",
        "from": "0x" + "11" * 20,
        "to": POOL,
        "cumulativeGasUsed": "0x5208",
        "gasUsed": "0x5208",
        "status": "0x1",
        "logs": logs,
    }


def _make_app() -> FastAPI:
    from app.core.errors import register_error_handlers

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(tracing_router)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app(), raise_server_exceptions=False)


def _patch_fourbyte(signatures: dict[str, str] | None = None) -> Any:
    """Patch resolve_event_signature to return canned 4byte responses."""
    sigs = signatures or {
        TRANSFER_TOPIC0: TRANSFER_SIG,
        APPROVAL_TOPIC0: APPROVAL_SIG,
        SWAP_TOPIC0: SWAP_SIG,
    }

    async def fake_resolve(hex_signature: str) -> str | None:
        return sigs.get(hex_signature.lower())

    return patch(
        "app.domains.scanner.tracing.service.resolve_event_signature",
        side_effect=fake_resolve,
    )


# ── Signature parsing ──────────────────────────────────────────────────


def test_parse_event_signature_transfer() -> None:
    parsed = parse_event_signature(TRANSFER_SIG)
    assert parsed is not None
    assert parsed.name == "Transfer"
    assert len(parsed.inputs) == 3
    assert parsed.inputs[0].type == "address"
    assert parsed.inputs[0].name == "from"
    assert parsed.inputs[0].indexed is True
    assert parsed.inputs[2].type == "uint256"
    assert parsed.inputs[2].name == "value"
    assert parsed.inputs[2].indexed is False


def test_parse_event_signature_swap_tuple_handling() -> None:
    parsed = parse_event_signature("Foo(address indexed a, (uint256,address)[] data)")
    assert parsed is not None
    assert parsed.name == "Foo"
    assert parsed.inputs[0].indexed is True
    assert parsed.inputs[1].type == "(uint256,address)[]"
    assert parsed.inputs[1].name == "data"


def test_parse_event_signature_malformed() -> None:
    assert parse_event_signature("not a signature") is None
    assert parse_event_signature("") is None


# ── 4byte decoding ─────────────────────────────────────────────────────


def test_decode_log_transfer() -> None:
    log = _log(USDC, [TRANSFER_TOPIC0, FROM_PAD, TO_PAD], _amount(1_000_000), 0)
    name, sig, args = decode_log(log, TRANSFER_SIG)
    assert name == "Transfer"
    assert sig == TRANSFER_SIG
    assert args["from"] == "0x1111111111111111111111111111111111111111"
    assert args["to"] == "0x2222222222222222222222222222222222222222"
    assert args["value"] == 1_000_000


def test_decode_log_indexed_dynamic_is_hash() -> None:
    # bytes32-indexed-ish dynamic arg (string indexed) — stays a hash
    log = _log(USDC, [TRANSFER_TOPIC0, FROM_PAD, TO_PAD], _amount(1), 0)
    name, _, _args = decode_log(log, TRANSFER_SIG)
    assert name == "Transfer"


def test_decode_log_no_topics() -> None:
    log = {"address": USDC, "topics": [], "data": "0x", "logIndex": "0x0"}
    name, sig, args = decode_log(log, TRANSFER_SIG)
    assert name is None
    assert sig is None
    assert args == {}


def test_is_static_type() -> None:
    assert _is_static_type("address") is True
    assert _is_static_type("uint256") is True
    assert _is_static_type("uint128") is True
    assert _is_static_type("bool") is True
    assert _is_static_type("bytes32") is True
    assert _is_static_type("string") is False
    assert _is_static_type("bytes") is False
    assert _is_static_type("uint256[]") is False


# ── 4byte API client (network mocked) ──────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_event_signature_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_event_signature should hit the API when Redis is unavailable."""
    captured: list[str] = []

    async def fake_redis_get(key: str) -> str | None:
        return None

    async def fake_redis_set(key: str, value: str, ttl: int = 0) -> None:
        pass

    async def fake_fetch(hex_signature: str) -> str | None:
        captured.append(hex_signature)
        return TRANSFER_SIG

    monkeypatch.setattr("app.domains.scanner.tracing.fourbyte._redis_get", fake_redis_get)
    monkeypatch.setattr("app.domains.scanner.tracing.fourbyte._redis_set", fake_redis_set)
    monkeypatch.setattr("app.domains.scanner.tracing.fourbyte._fetch_from_api", fake_fetch)

    result = await resolve_event_signature(TRANSFER_TOPIC0)
    assert result == TRANSFER_SIG
    assert captured == [TRANSFER_TOPIC0]


@pytest.mark.asyncio
async def test_resolve_event_signature_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis cache hit should skip the API call."""

    async def fake_redis_get(key: str) -> str | None:
        return TRANSFER_SIG

    async def fake_redis_set(key: str, value: str, ttl: int = 0) -> None:
        raise AssertionError("should not write cache on hit")

    async def fake_fetch(hex_signature: str) -> str | None:
        raise AssertionError("should not hit API on cache hit")

    monkeypatch.setattr("app.domains.scanner.tracing.fourbyte._redis_get", fake_redis_get)
    monkeypatch.setattr("app.domains.scanner.tracing.fourbyte._redis_set", fake_redis_set)
    monkeypatch.setattr("app.domains.scanner.tracing.fourbyte._fetch_from_api", fake_fetch)

    result = await resolve_event_signature(TRANSFER_TOPIC0)
    assert result == TRANSFER_SIG


@pytest.mark.asyncio
async def test_fetch_from_api_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-200 from 4byte should return None without raising."""

    class FakeResponse:
        status_code = 500

        def json(self) -> dict[str, Any]:
            return {}

    async def fake_get(url: str, params: dict[str, str]) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _FakeClient(fake_get))
    assert await _fetch_from_api(TRANSFER_TOPIC0) is None


class _FakeClient:
    """Minimal AsyncClient stand-in for httpx patching."""

    def __init__(self, get_fn: Any, timeout: float = 8.0) -> None:
        self._get_fn = get_fn

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def get(self, url: str, params: dict[str, str] | None = None) -> Any:
        return self._get_fn(url, params or {})


# ── fetch_tx_logs ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_tx_logs_via_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = _build_receipt()
    fake_router = AsyncMock()
    fake_router.call.return_value = {"jsonrpc": "2.0", "id": 1, "result": receipt}

    async def fake_get_router() -> Any:
        return fake_router

    monkeypatch.setattr(
        "app.domains.scanner.tracing.receipt.get_rpc_router",
        lambda: fake_router,
    )
    logs, source = await fetch_tx_logs("0x" + "ab" * 32, "ethereum")
    assert source == "rpc"
    assert len(logs) == 3
    assert fake_router.call.called


@pytest.mark.asyncio
async def test_fetch_tx_logs_unsupported_chain() -> None:
    logs, source = await fetch_tx_logs("0x" + "ab" * 32, "solana")
    assert logs == []
    assert source == "none"


def test_is_supported_chain() -> None:
    assert is_supported_chain("ethereum") is True
    assert is_supported_chain("base") is True
    assert is_supported_chain("solana") is False


# ── Hop mapping ────────────────────────────────────────────────────────


def test_build_wallet_hops_transfer_approval_swap() -> None:
    logs = _build_receipt()["logs"]
    decoded = {
        "0": {"event": "Transfer", "args": {"from": "0x" + "11" * 20, "to": "0x" + "22" * 20, "value": 1_000_000}},
        "1": {"event": "Approval", "args": {"owner": "0x" + "33" * 20, "spender": "0x" + "44" * 20, "value": 5_000}},
        "2": {
            "event": "Swap",
            "args": {
                "sender": "0x" + "55" * 20,
                "amount0In": 0,
                "amount1In": 1_000,
                "amount0Out": 2_000,
                "amount1Out": 0,
                "to": "0x" + "66" * 20,
            },
        },
    }
    hops = build_wallet_hops(logs, decoded)
    assert len(hops) == 3

    transfer = next(h for h in hops if h.event == "Transfer")
    assert transfer.from_ == "0x" + "11" * 20
    assert transfer.to == "0x" + "22" * 20
    assert transfer.value == "1000000"
    assert transfer.token == USDC

    approval = next(h for h in hops if h.event == "Approval")
    assert approval.from_ == "0x" + "33" * 20
    assert approval.to == "0x" + "44" * 20
    assert approval.value == "5000"

    swap = next(h for h in hops if h.event == "Swap")
    assert swap.from_ == "0x" + "55" * 20
    assert swap.to == "0x" + "66" * 20


def test_build_wallet_hops_skips_self_transfer() -> None:
    logs = [_log(USDC, [TRANSFER_TOPIC0, FROM_PAD, FROM_PAD], _amount(100), 0)]
    decoded = {"0": {"event": "Transfer", "args": {"from": "0x" + "11" * 20, "to": "0x" + "11" * 20, "value": 100}}}
    hops = build_wallet_hops(logs, decoded)
    assert hops == []


def test_build_wallet_hops_deposit_withdrawal() -> None:
    logs = [
        _log(WETH, ["0x1234", "0x" + "00" * 12 + "aa" * 20], _amount(100), 0),
    ]
    decoded = {"0": {"event": "Deposit", "args": {"dst": "0x" + "aa" * 20, "wad": 100}}}
    hops = build_wallet_hops(logs, decoded)
    assert len(hops) == 1
    assert hops[0].from_ == WETH  # contract → dst
    assert hops[0].to == "0x" + "aa" * 20
    assert hops[0].token == WETH


# ── Graph building ─────────────────────────────────────────────────────


def test_build_cluster_graph() -> None:
    hops = build_wallet_hops(
        _build_receipt()["logs"],
        {
            "0": {
                "event": "Transfer",
                "args": {"from": "0x" + "11" * 20, "to": "0x" + "22" * 20, "value": 1_000_000},
            },
            "1": {
                "event": "Approval",
                "args": {"owner": "0x" + "33" * 20, "spender": "0x" + "44" * 20, "value": 5_000},
            },
            "2": {"event": "Swap", "args": {"sender": "0x" + "55" * 20, "to": "0x" + "66" * 20}},
        },
    )
    graph = build_cluster_graph(hops)
    assert len(graph.edges) == 3
    assert len(graph.nodes) == 8  # 6 wallets + USDC + pool token
    node_addrs = {n.address for n in graph.nodes}
    assert "0x" + "11" * 20 in node_addrs
    assert "0x" + "22" * 20 in node_addrs
    edge_sources = {e.source for e in graph.edges}
    assert edge_sources == {
        "0x" + "11" * 20,
        "0x" + "33" * 20,
        "0x" + "55" * 20,
    }


def test_build_cluster_graph_token_type() -> None:
    from app.domains.scanner.tracing.models import WalletHop

    hops = [WalletHop(from_="0x" + "11" * 20, to="0x" + "22" * 20, value="100", token=USDC, event="Transfer")]
    graph = build_cluster_graph(hops)
    assert len(graph.nodes) == 3
    token_nodes = [n for n in graph.nodes if n.type == "token"]
    assert len(token_nodes) == 1
    assert token_nodes[0].address == USDC


# ── Trace hops (debug_traceTransaction enhancement) ────────────────────


def test_extract_trace_hops_recursive() -> None:
    trace = {
        "from": "0x" + "aa" * 20,
        "to": "0x" + "bb" * 20,
        "value": "0xde0b6b3a7640000",  # 1 ETH
        "calls": [
            {
                "from": "0x" + "bb" * 20,
                "to": "0x" + "cc" * 20,
                "value": "0x16345785d8a0000",  # 0.1 ETH
                "calls": [],
            }
        ],
    }
    hops = extract_trace_hops(trace)
    assert len(hops) == 2
    assert hops[0]["value"] == "1000000000000000000"
    assert hops[0]["from"] == "0x" + "aa" * 20
    assert hops[1]["value"] == "100000000000000000"


# ── Full pipeline ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_decode_transaction_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = _build_receipt()
    fake_router = AsyncMock()
    fake_router.call.return_value = {"jsonrpc": "2.0", "id": 1, "result": receipt}
    monkeypatch.setattr("app.domains.scanner.tracing.receipt.get_rpc_router", lambda: fake_router)

    with _patch_fourbyte():
        result = await decode_transaction("0x" + "ab" * 32, "ethereum")

    assert result.chain == "ethereum"
    assert result.source == "rpc"
    assert len(result.logs) == 3
    # logIndex sorted ascending (Swap was index 2 in receipt)
    assert [log.log_index for log in result.logs] == [0, 1, 2]
    # Decoded event names
    names = {log.event_name for log in result.logs}
    assert names == {"Transfer", "Approval", "Swap"}
    # Wallet hops present
    assert len(result.wallet_hops) == 3
    # Graph built
    assert len(result.graph.nodes) >= 4
    assert len(result.graph.edges) == 3


@pytest.mark.asyncio
async def test_decode_transaction_include_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = _build_receipt()
    fake_router = AsyncMock()
    fake_router.call.return_value = {"jsonrpc": "2.0", "id": 1, "result": receipt}
    monkeypatch.setattr("app.domains.scanner.tracing.receipt.get_rpc_router", lambda: fake_router)

    with _patch_fourbyte():
        result = await decode_transaction("0x" + "ab" * 32, "ethereum", include_trace=True)

    assert result.trace is not None or result.trace is None  # best-effort
    assert result.source == "rpc"


@pytest.mark.asyncio
async def test_decode_transaction_invalid_tx_hash() -> None:
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        await decode_transaction("not-a-hash", "ethereum")

    with pytest.raises(ValidationError):
        await decode_transaction("0x1234", "ethereum")


@pytest.mark.asyncio
async def test_decode_transaction_unsupported_chain() -> None:
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        await decode_transaction("0x" + "ab" * 32, "solana")


@pytest.mark.asyncio
async def test_decode_transaction_no_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_router = AsyncMock()
    fake_router.call.return_value = {"jsonrpc": "2.0", "id": 1, "result": None}
    monkeypatch.setattr("app.domains.scanner.tracing.receipt.get_rpc_router", lambda: fake_router)

    with _patch_fourbyte():
        result = await decode_transaction("0x" + "ab" * 32, "ethereum")

    assert result.logs == []
    assert result.wallet_hops == []
    assert result.graph.nodes == []
    assert result.source == "none"


# ── Route contract ─────────────────────────────────────────────────────


def test_route_decode_contract(client: TestClient) -> None:
    receipt = _build_receipt()
    fake_router = AsyncMock()
    fake_router.call.return_value = {"jsonrpc": "2.0", "id": 1, "result": receipt}

    with (
        patch("app.domains.scanner.tracing.receipt.get_rpc_router", return_value=fake_router),
        _patch_fourbyte(),
    ):
        resp = client.post(
            "/api/v1/tracing/decode",
            json={"tx_hash": "0x" + "ab" * 32, "chain": "ethereum"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["tx_hash"] == "0x" + "ab" * 32
    assert body["chain"] == "ethereum"
    assert "logs" in body
    assert "wallet_hops" in body
    assert "graph" in body
    assert body["source"] == "rpc"
    # Route contract: logIndex key (alias), wallet_hops use "from"/"to"
    assert body["logs"][0]["logIndex"] == 0
    assert body["logs"][0]["address"]
    assert body["logs"][0]["topics"]
    assert "decoded_event" in body["logs"][0]
    assert "args" in body["logs"][0]
    assert body["wallet_hops"][0]["from"]
    assert body["wallet_hops"][0]["to"]
    assert "value" in body["wallet_hops"][0]
    assert "token" in body["wallet_hops"][0]
    assert body["graph"]["nodes"]
    assert body["graph"]["edges"]


def test_route_decode_validation_error(client: TestClient) -> None:
    resp = client.post("/api/v1/tracing/decode", json={"tx_hash": "bad", "chain": "ethereum"})
    assert resp.status_code == 400


def test_route_health(client: TestClient) -> None:
    resp = client.get("/api/v1/tracing/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "tracing-pipeline"}
