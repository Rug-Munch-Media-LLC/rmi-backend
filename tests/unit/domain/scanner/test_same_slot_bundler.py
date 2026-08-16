"""Unit tests for ``own_bundle_detect.detect_same_slot_bundler``.

Covers the Helius-backed same-slot concentration scan (Solana) and the
fail-open ``[REVIEW]`` fallback (EVM chains / Helius unreachable) that
surfaces the existing bundle-detection result with wallet count + confidence.
No real network — ``app.chain_client.ChainClient`` is stubbed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.scanner.engines import own_bundle_detect as bd

MINT = "So11111111111111111111111111111111111111111"
WALLET_A = "WalletA1111111111111111111111111111111111111111"
WALLET_B = "WalletB1111111111111111111111111111111111111111"
WALLET_C = "WalletC1111111111111111111111111111111111111111"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    bd.clear_cache()
    yield
    bd.clear_cache()


def _tx(slot: int, signer: str, sol_spent_lamports: int, pre_tokens: float, post_tokens: float) -> dict[str, Any]:
    """A Helius ``getTransaction`` result for one buy tx."""
    return {
        "slot": slot,
        "transaction": {"message": {"accountKeys": [{"pubkey": signer, "signer": True, "writable": True}]}},
        "meta": {
            "preBalances": [1_000_000_000 + sol_spent_lamports],
            "postBalances": [1_000_000_000],
            "preTokenBalances": [{"mint": MINT, "owner": signer, "uiTokenAmount": {"uiAmount": pre_tokens}}],
            "postTokenBalances": [{"mint": MINT, "owner": signer, "uiTokenAmount": {"uiAmount": post_tokens}}],
        },
    }


class _FakeChainClient:
    """ChainClient stub — maps RPC method → canned result (or raises)."""

    def __init__(self, responses: dict[str, Any], raise_exc: Exception | None = None) -> None:
        self._responses = responses
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, list[Any]]] = []

    async def rpc_call(self, method: str, params: list[Any]) -> Any:
        self.calls.append((method, params))
        if self._raise_exc is not None:
            raise self._raise_exc
        if method == "getTransaction":
            return {"result": self._responses[method].pop(0)}
        if method == "getTokenAccountsByOwner":
            wallet = params[0]
            by_wallet = self._responses.get("holder_amounts", {})
            ui = by_wallet.get(wallet, 0.0)
            return {
                "result": {
                    "value": [
                        {
                            "account": {
                                "data": {
                                    "parsed": {"info": {"tokenAmount": {"uiAmount": ui}}},
                                }
                            }
                        }
                    ]
                }
            }
        return self._responses.get(method)


def _install_client(monkeypatch: pytest.MonkeyPatch, client: _FakeChainClient) -> None:
    monkeypatch.setattr("app.chain_client.ChainClient", lambda: client)


# ── Scanned path (Solana + Helius) ──────────────────────────────────────────


def test_same_slot_scanned_with_concentration(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_launch(token_address: str, chain: str, force_fresh: bool = False) -> dict[str, Any]:
        return {"is_bundled": False, "confidence": 0.0, "launch_buyers": 0, "result": "unavailable"}

    monkeypatch.setattr(bd, "detect_bundled_launch", fake_launch)
    client = _FakeChainClient(
        {
            "getSignaturesForAddress": {
                "result": [
                    {"signature": "sig1", "slot": 100},
                    {"signature": "sig2", "slot": 100},
                    {"signature": "sig3", "slot": 101},
                ]
            },
            "getTransaction": [
                _tx(100, WALLET_A, sol_spent_lamports=2_000_000_000, pre_tokens=0.0, post_tokens=50.0),
                _tx(100, WALLET_B, sol_spent_lamports=3_000_000_000, pre_tokens=0.0, post_tokens=30.0),
                _tx(101, WALLET_C, sol_spent_lamports=1_000_000_000, pre_tokens=0.0, post_tokens=10.0),
            ],
            "getTokenSupply": {"result": {"value": {"uiAmount": 1000.0}}},
            "holder_amounts": {WALLET_A: 50.0, WALLET_B: 30.0},
        }
    )
    _install_client(monkeypatch, client)

    result = asyncio.run(bd.detect_same_slot_bundler(MINT, "solana"))
    assert result["result"] == "scanned"
    assert result["source"] == "helius"
    assert result["slot_analysis_source"] == "helius"
    assert result["same_slot"] == 100  # busiest slot: 2 wallets
    assert result["same_slot_wallets"] == 2
    assert result["same_slot_sol_spent"] == pytest.approx(5.0)  # 2 SOL + 3 SOL
    assert result["same_slot_held_pct"] == pytest.approx(8.0)  # (50 + 30) / 1000
    assert result["note"] == ""
    # 101 (single wallet) is not a same-slot cluster.
    assert all(s["slot"] != 101 for s in result["slots"])


def test_same_slot_parse_skips_non_buys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bd, "detect_bundled_launch", _noop_launch())
    client = _FakeChainClient(
        {
            "getSignaturesForAddress": {"result": [{"signature": "sell1", "slot": 200}]},
            "getTransaction": [
                _tx(200, WALLET_A, sol_spent_lamports=1_000_000_000, pre_tokens=50.0, post_tokens=0.0),  # sell
            ],
            "getTokenSupply": {"result": {"value": {"uiAmount": 1000.0}}},
            "getTokenAccountsByOwner": {"result": {"value": []}},
        }
    )
    _install_client(monkeypatch, client)

    result = asyncio.run(bd.detect_same_slot_bundler(MINT, "solana"))
    # No buy parsed → Helius path yields no concentration → [REVIEW] fallback.
    assert result["result"] == "unavailable"
    assert result["slot_analysis_source"] == "unavailable"
    assert "[REVIEW]" in result["note"]


def test_same_slot_evm_returns_solana_only_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bd, "detect_bundled_launch", _noop_launch())
    result = asyncio.run(bd.detect_same_slot_bundler("0x" + "aa" * 20, "ethereum"))
    assert result["result"] == "unavailable"
    assert "Solana-only" in result["note"]
    assert "[REVIEW]" in result["note"]


def test_same_slot_fallback_surfaces_existing_bundle_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_launch(token_address: str, chain: str, force_fresh: bool = False) -> dict[str, Any]:
        return {"is_bundled": True, "confidence": 72.0, "launch_buyers": 9, "result": "scanned"}

    monkeypatch.setattr(bd, "detect_bundled_launch", fake_launch)
    # Helius unreachable (raises immediately).
    _install_client(monkeypatch, _FakeChainClient({}, raise_exc=RuntimeError("helius down")))

    result = asyncio.run(bd.detect_same_slot_bundler(MINT, "solana"))
    assert result["result"] == "unavailable"
    assert result["is_bundled"] is True
    assert result["confidence"] == pytest.approx(72.0)
    assert result["bundle_wallets"] == 9
    assert "[REVIEW]" in result["note"]


def _noop_launch() -> Any:
    async def _fake(token_address: str, chain: str, force_fresh: bool = False) -> dict[str, Any]:
        return {"is_bundled": False, "confidence": 0.0, "launch_buyers": 0, "result": "unavailable"}

    return _fake


# ── Pure tx parser ──────────────────────────────────────────────────────────


def test_parse_solana_buy_tx_positive() -> None:
    parsed = bd._parse_solana_buy_tx(
        _tx(100, WALLET_A, sol_spent_lamports=2_000_000_000, pre_tokens=0.0, post_tokens=50.0), MINT
    )
    assert parsed is not None
    assert parsed["slot"] == 100
    assert parsed["signer"] == WALLET_A
    assert parsed["sol_spent"] == pytest.approx(2.0)
    assert parsed["token_delta"] == pytest.approx(50.0)


def test_parse_solana_buy_tx_rejects_sell() -> None:
    parsed = bd._parse_solana_buy_tx(
        _tx(100, WALLET_A, sol_spent_lamports=1_000_000_000, pre_tokens=50.0, post_tokens=0.0), MINT
    )
    assert parsed is None


def test_parse_solana_buy_tx_rejects_empty_meta() -> None:
    assert bd._parse_solana_buy_tx({}, MINT) is None
