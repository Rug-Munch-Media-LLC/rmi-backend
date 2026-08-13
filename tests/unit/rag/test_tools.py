"""Unit tests for the real-time tool registry (``app.rag.tools``).

Each tool is a thin, fail-open wrapper around a RugMunch data provider:
it NEVER raises — on provider failure it returns ``{"error": "unavailable"}``
so the agentic graph keeps flowing.

Covered here:

  1. ``get_token_price`` — EVM 0x addresses route to DexScreener;
     tickers/ids (incl. SOL) route to CoinGecko.
  2. ``get_gas_price`` — public RPC, hex wei → gwei conversion.
  3. ``get_wallet_balance`` — delegates to web3_chat balances, fails open.
  4. ``scan_token`` — delegates to RugMunchScan v2 (SENTINEL).
  5. ``get_news`` — HybridRetriever over the news collection.
  6. Fail-open behavior for every tool when its provider errors.
  7. Chain inference: 0x → ethereum, base58 → solana, explicit param wins.

All external providers (httpx, web3_chat balances, scanner, retriever) are
mocked; no network traffic occurs.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.rag.tools import (
    extract_address,
    get_gas_price,
    get_news,
    get_token_price,
    get_wallet_balance,
    scan_token,
)

# ── Fixtures / helpers ─────────────────────────────────────────────────

EVM_ADDR = "0x1234567890123456789012345678901234567890"
SOL_ADDR = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint


def _httpx_response(json_data: Any, status: int = 200) -> MagicMock:
    """A synchronous httpx.Response stand-in (raise_for_status/json are sync)."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    resp.status_code = status
    return resp


@contextmanager
def _patch_httpx_client(*, get_return=None, post_return=None, get_exc=None, post_exc=None):
    """Patch ``httpx.AsyncClient`` with a fake async context-manager client.

    ``httpx.AsyncClient`` is imported lazily inside each tool, so patching
    the module attribute intercepts every call site.
    """
    client = AsyncMock()
    if get_exc is not None:
        client.get.side_effect = get_exc
    else:
        client.get.return_value = get_return
    if post_exc is not None:
        client.post.side_effect = post_exc
    else:
        client.post.return_value = post_return

    with patch("httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        yield client


# ── get_token_price ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_token_price_evm() -> None:
    """EVM 0x address → DexScreener contract search → price returned."""
    dexscreener_resp = _httpx_response(
        {"pairs": [{"priceUsd": "1.234", "chainId": "ethereum", "baseToken": {"symbol": "TEST"}}]}
    )
    with _patch_httpx_client(get_return=dexscreener_resp) as client:
        result = await get_token_price(EVM_ADDR)

    assert result["price_usd"] == 1.234
    assert result["source"] == "DexScreener"
    assert result["chain"] == "ethereum"
    assert result["token"] == EVM_ADDR
    # It hit DexScreener, not CoinGecko.
    assert "dexscreener.com" in client.get.call_args.args[0]
    assert "coingecko.com" not in client.get.call_args.args[0]


@pytest.mark.asyncio
async def test_get_token_price_solana() -> None:
    """SOL ticker → CoinGecko simple price → price returned."""
    coingecko_resp = _httpx_response({"solana": {"usd": 150.5}})
    with _patch_httpx_client(get_return=coingecko_resp) as client:
        result = await get_token_price("SOL")

    assert result["price_usd"] == 150.5
    assert result["source"] == "CoinGecko"
    assert result["token"] == "SOL"
    assert "coingecko.com" in client.get.call_args.args[0]


@pytest.mark.asyncio
async def test_get_token_price_unknown_id_returns_error() -> None:
    """CoinGecko returns nothing for the id → structured error, no raise."""
    coingecko_resp = _httpx_response({"not-the-coin": {"usd": 1.0}})
    with _patch_httpx_client(get_return=coingecko_resp):
        result = await get_token_price("TOTALLY_UNKNOWN_TICKER")

    assert result["price_usd"] is None
    assert result["error"] == "unknown_id"


# ── get_gas_price ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_gas_price() -> None:
    """RPC returns hex wei → converted to gwei."""
    rpc_resp = _httpx_response({"jsonrpc": "2.0", "result": "0x3b9aca00"})  # 1 gwei
    with _patch_httpx_client(post_return=rpc_resp) as client:
        result = await get_gas_price()

    assert result["gas_price_gwei"] == 1.0
    assert result["source"] == "public_rpc"
    assert result["chain"] == "ethereum"
    assert "publicnode.com" in client.post.call_args.args[0]
    # RPC request body is the eth_gasPrice JSON-RPC call.
    body = client.post.call_args.kwargs["json"]
    assert body["method"] == "eth_gasPrice"


@pytest.mark.asyncio
async def test_get_gas_price_no_result_returns_error() -> None:
    """RPC returns no result → structured error, no raise."""
    rpc_resp = _httpx_response({"jsonrpc": "2.0", "result": None})
    with _patch_httpx_client(post_return=rpc_resp):
        result = await get_gas_price()

    assert result["gas_price_gwei"] is None
    assert result["error"] == "no_result"


# ── get_wallet_balance ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_wallet_balance() -> None:
    """Delegates to web3_chat balances and tags address + inferred chain."""
    with patch(
        "app.services.web3_chat.balances.get_wallet_balance",
        new=AsyncMock(return_value={"balance": "1.5", "token": "ETH"}),
    ) as wb:
        result = await get_wallet_balance(f"what is the balance of {EVM_ADDR}?")

    assert result["address"] == EVM_ADDR
    assert result["chain"] == "ethereum"
    assert result["balance"] == "1.5"
    assert result["token"] == "ETH"
    wb.assert_awaited_once_with(EVM_ADDR, "ethereum")


# ── scan_token ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_token() -> None:
    """Delegates to RugMunchScan v2 and surfaces safety fields."""
    scan_result = SimpleNamespace(safety_score=85, risk_level="low", risk_flags=["fake_volume"])
    with patch("app.domains.scanner.scan_token", new=AsyncMock(return_value=scan_result)) as scanner:
        result = await scan_token(f"check {EVM_ADDR} for honeypot")

    assert result["address"] == EVM_ADDR
    assert result["chain"] == "ethereum"
    assert result["safety_score"] == 85
    assert result["risk_level"] == "low"
    assert result["risk_flags"] == ["fake_volume"]
    assert result["source"] == "RugMunchScan v2"
    scanner.assert_awaited_once()
    assert scanner.await_args.kwargs["tiers"] == ["free"]


# ── get_news ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_news() -> None:
    """Queries HybridRetriever over the news_v2 collection and shapes hits."""
    hits = [
        SimpleNamespace(
            content="Bitcoin ETF inflows surge", final_score=0.85, score=0.8, collection="news_v2", doc_id="news:1"
        )
    ]
    retriever = AsyncMock()
    retriever.retrieve.return_value = hits
    with patch("app.rag.retrieval.HybridRetriever", return_value=retriever) as hr:
        result = await get_news("bitcoin etf")

    assert result["count"] == 1
    assert result["results"][0]["content"] == "Bitcoin ETF inflows surge"
    assert result["results"][0]["score"] == 0.85
    assert result["results"][0]["doc_id"] == "news:1"
    assert result["source"] == "RAG news_v2"
    hr.assert_called_once_with()
    retriever.retrieve.assert_awaited_once_with(query="bitcoin etf", collection="news_v2", top_k=5, rerank=True)


# ── Fail-open ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tools_fail_open() -> None:
    """Every tool returns an error dict when its provider fails — no raises."""
    # get_token_price: HTTP failure
    with _patch_httpx_client(get_exc=httpx.ConnectError("connection refused")):
        result = await get_token_price("BTC")
    assert result["error"] == "unavailable"

    # get_gas_price: HTTP failure
    with _patch_httpx_client(post_exc=httpx.ConnectError("connection refused")):
        result = await get_gas_price()
    assert result["error"] == "unavailable"

    # get_gas_price: malformed hex → ValueError caught → unavailable
    with _patch_httpx_client(post_return=_httpx_response({"result": "0xZZZ"})):
        result = await get_gas_price()
    assert result["error"] == "unavailable"

    # get_wallet_balance: provider failure
    with patch(
        "app.services.web3_chat.balances.get_wallet_balance",
        new=AsyncMock(side_effect=RuntimeError("rpc down")),
    ):
        result = await get_wallet_balance(f"balance of {EVM_ADDR}")
    assert result["error"] == "unavailable"

    # scan_token: scanner failure
    with patch("app.domains.scanner.scan_token", new=AsyncMock(side_effect=RuntimeError("scanner down"))):
        result = await scan_token(EVM_ADDR)
    assert result["error"] == "unavailable"

    # get_news: retrieval failure
    retriever = AsyncMock()
    retriever.retrieve.side_effect = ConnectionError("rag down")
    with patch("app.rag.retrieval.HybridRetriever", return_value=retriever):
        result = await get_news("bitcoin")
    assert result["error"] == "unavailable"

    # Missing address → structured error, not a raise.
    no_addr = await get_wallet_balance("just a question about defi")
    assert no_addr["error"] == "no_address_in_query"
    no_addr_scan = await scan_token("just a question about defi")
    assert no_addr_scan["error"] == "no_address_in_query"


# ── Chain inference ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chain_inference_solana() -> None:
    """Non-0x (base58) address → solana for balance and scan tools."""
    with patch(
        "app.services.web3_chat.balances.get_wallet_balance",
        new=AsyncMock(return_value={"balance": "10.0"}),
    ) as wb:
        result = await get_wallet_balance(f"SOL wallet {SOL_ADDR}")
    assert result["chain"] == "solana"
    wb.assert_awaited_once_with(SOL_ADDR, "solana")

    scan_result = SimpleNamespace(safety_score=0, risk_level="low", risk_flags=[])
    with patch("app.domains.scanner.scan_token", new=AsyncMock(return_value=scan_result)) as scanner:
        result = await scan_token(SOL_ADDR)
    assert result["chain"] == "solana"
    assert scanner.await_args.kwargs["chain"] == "solana"


@pytest.mark.asyncio
async def test_chain_inference_evm() -> None:
    """0x address → ethereum for balance and scan tools."""
    with patch(
        "app.services.web3_chat.balances.get_wallet_balance",
        new=AsyncMock(return_value={"balance": "1.5"}),
    ) as wb:
        result = await get_wallet_balance(f"balance of {EVM_ADDR}")
    assert result["chain"] == "ethereum"
    wb.assert_awaited_once_with(EVM_ADDR, "ethereum")


@pytest.mark.asyncio
async def test_chain_inference_explicit() -> None:
    """An explicit chain param overrides address-based inference."""
    with patch(
        "app.services.web3_chat.balances.get_wallet_balance",
        new=AsyncMock(return_value={"balance": "1.5"}),
    ) as wb:
        result = await get_wallet_balance(f"balance of {EVM_ADDR}", chain="bsc")
    assert result["chain"] == "bsc"
    wb.assert_awaited_once_with(EVM_ADDR, "bsc")

    # scan_token preserves the explicit chain for EVM addresses.
    scan_result = SimpleNamespace(safety_score=90, risk_level="low", risk_flags=[])
    with patch("app.domains.scanner.scan_token", new=AsyncMock(return_value=scan_result)) as scanner:
        result = await scan_token(EVM_ADDR, chain="bsc")
    assert result["chain"] == "bsc"
    assert scanner.await_args.kwargs["chain"] == "bsc"


# ── address extraction ─────────────────────────────────────────────────


def test_extract_address_finds_evm_and_solana() -> None:
    assert extract_address(f"wallet {EVM_ADDR} here") == EVM_ADDR
    assert extract_address(f"wallet {SOL_ADDR} here") == SOL_ADDR
    assert extract_address("no address in this text") is None
