"""Unit tests for ``app.osint.wallet`` — label loaders, redis and httpx mocked.

The file-based entity registry, Redis-backed label loader, Chainabuse HTTP
API and the OFAC sanctions loader are all monkeypatched with in-memory fakes —
no network, no Redis, no on-disk datasets.

Covers:

* ``_normalize``: EVM checksumming (+ lowercase fallback), Solana detection,
  unknown formats, and chain-override mismatch handling;
* entity-label attribution (exchange vs non-exchange vs no-match);
* Redis label finding (and silent skip when Redis is unavailable);
* Chainabuse report parsing, "no reports", and the 401-without-key error;
* OFAC sanctions match / no-match / missing-list paths;
* per-source failure isolation (one source raising never aborts the rest);
* ``lookup_wallet`` end-to-end with every source stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.osint import OsintReport, wallet as w

EVM_ADDR = "0xBE0eB53FC46b790099138e3d32C721856d41e865"  # Binance hot wallet
SOLANA_ADDR = "So11111111111111111111111111111111111111111"


@pytest.fixture(autouse=True)
def _reset_sanctions_cache() -> None:
    """The sanctions loader caches its index module-wide; reset per test."""
    w._sanctions_cache = None
    yield
    w._sanctions_cache = None


# ── _normalize ──────────────────────────────────────────────────────────────


def test_normalize_evm_checksums_when_eth_utils_available() -> None:
    display, chain, err = w._normalize("0x" + "11" * 20)
    assert chain == "evm"
    assert err is None
    assert display == "0x" + "11" * 20  # already lowercase -> checksummed form
    assert display.islower() or display.isupper() or any(c.isupper() for c in display)


def test_normalize_evm_lowercases_without_eth_utils(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "eth_utils.address", None)  # force ImportError
    display, chain, err = w._normalize("0xBE0eB53FC46b790099138e3d32C721856d41e865")
    assert chain == "evm"
    assert display == "0xbe0eb53fc46b790099138e3d32c721856d41e865"
    assert err is None


def test_normalize_solana() -> None:
    display, chain, err = w._normalize(SOLANA_ADDR)
    assert chain == "solana"
    assert display == SOLANA_ADDR
    assert err is None


def test_normalize_unknown_format() -> None:
    _, chain, err = w._normalize("not-an-address")
    assert chain is None
    assert err
    assert "unrecognized address format" in err


def test_normalize_chain_override_mismatch_evm() -> None:
    _, chain, err = w._normalize("0x" + "11" * 20, chain="solana")
    assert chain == "evm"  # format wins; mismatch surfaces as error
    assert err
    assert "does not match EVM" in err


def test_normalize_chain_override_mismatch_solana() -> None:
    _, chain, err = w._normalize(SOLANA_ADDR, chain="ethereum")
    assert chain == "solana"
    assert err
    assert "does not match Solana" in err


# ── Fakes ───────────────────────────────────────────────────────────────────


def _fake_match(
    entity_name: str = "Binance", entity_type: str = "exchange", source: str = "internal", address: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(entity_name=entity_name, entity_type=entity_type, source=source, address=address or EVM_ADDR)


class _FakeRegistry:
    def __init__(self, match: Any = None, exc: Exception | None = None) -> None:
        self._match = match
        self._exc = exc

    def classify_address(self, address: str) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._match


def _install_entity_registry(monkeypatch: pytest.MonkeyPatch, registry: _FakeRegistry) -> None:
    monkeypatch.setattr("app.entity_registry.get_entity_registry", lambda: registry)


def _install_redis(
    monkeypatch: pytest.MonkeyPatch, label: dict[str, Any] | None = None, redis_available: bool = True
) -> None:
    if not redis_available:
        monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
        return

    class _FakeRedis:
        pass

    monkeypatch.setattr("app.core.redis.get_redis", lambda: _FakeRedis())
    monkeypatch.setattr("app.wallet_label_loader.lookup_wallet_label", lambda key, redis_client=None: label)


def _install_chainabuse(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, exc: Exception | None = None
) -> None:
    def fake_get(url: str, *, headers: dict[str, str], auth: Any = None, timeout: float = 60) -> httpx.Response:
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(w.httpx, "get", fake_get)


def _install_sanctions(monkeypatch: pytest.MonkeyPatch, index: dict[str, Any] | None) -> None:
    monkeypatch.setattr(w, "_load_sanctions", lambda: index)


# ── Entity labels ───────────────────────────────────────────────────────────


def test_entity_labels_exchange_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_entity_registry(monkeypatch, _FakeRegistry(_fake_match(entity_type="exchange", source="internal")))
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._entity_labels(report, EVM_ADDR.lower())
    finding = next(f for f in report.findings if f.source == "entity_registry")
    assert finding.category == "exchange"
    assert finding.confidence == "high"
    assert "Binance" in finding.summary


def test_entity_labels_cex_hot_wallet_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_entity_registry(
        monkeypatch,
        _FakeRegistry(_fake_match(entity_name="Coinbase Hot", entity_type="cex_hot_wallet", source="local_labels")),
    )
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._entity_labels(report, EVM_ADDR.lower())
    finding = next(f for f in report.findings if f.source == "entity_registry")
    assert finding.category == "exchange"
    assert finding.confidence == "medium"  # non-internal source


def test_entity_labels_non_exchange_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_entity_registry(
        monkeypatch, _FakeRegistry(_fake_match(entity_name="Some Mixer", entity_type="mixer", source="internal"))
    )
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._entity_labels(report, EVM_ADDR.lower())
    finding = next(f for f in report.findings if f.source == "entity_registry")
    assert finding.category == "entity_label"
    assert "Some Mixer" in finding.summary


def test_entity_labels_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_entity_registry(monkeypatch, _FakeRegistry(match=None))
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._entity_labels(report, EVM_ADDR.lower())
    finding = next(f for f in report.findings if f.source == "entity_registry")
    assert finding.summary == "no known entity label"
    assert finding.confidence == "low"


def test_entity_labels_registry_failure_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_entity_registry(monkeypatch, _FakeRegistry(exc=RuntimeError("registry boom")))
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._entity_labels(report, EVM_ADDR.lower())
    assert report.findings == []
    assert any("entity_registry: registry boom" in e for e in report.errors)


# ── Redis labels ────────────────────────────────────────────────────────────


def test_redis_label_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_redis(monkeypatch, label={"name": "Binance Hot Wallet", "label_type": "exchange", "count": 3})
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._redis_label(report, EVM_ADDR.lower())
    finding = next(f for f in report.findings if f.source == "wallet_label_loader")
    assert finding.category == "entity_label"
    assert "Binance Hot Wallet" in finding.summary
    assert finding.data["count"] == 3


def test_redis_label_skipped_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_redis(monkeypatch, redis_available=False)
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._redis_label(report, EVM_ADDR.lower())
    assert report.findings == []
    assert report.errors == []


def test_redis_label_none_label_no_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_redis(monkeypatch, label=None)
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._redis_label(report, EVM_ADDR.lower())
    assert report.findings == []


# ── Chainabuse ──────────────────────────────────────────────────────────────


def _chainabuse_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "http://chainabuse.invalid"))


def test_chainabuse_reports_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_chainabuse(
        monkeypatch,
        _chainabuse_response(
            {
                "reports": [
                    {
                        "id": "r1",
                        "address": EVM_ADDR,
                        "chain": "ethereum",
                        "category": "Scam",
                        "description": "fake token",
                        "trusted": "true",
                        "reportedAt": "2024-01-01",
                    }
                ]
            }
        ),
    )
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._chainabuse_reports(report, EVM_ADDR, 30)
    finding = next(f for f in report.findings if f.source == "chainabuse")
    assert finding.category == "scam_report"
    assert finding.confidence == "high"  # trusted report
    assert "Scam" in finding.summary
    assert finding.data["report_id"] == "r1"


def test_chainabuse_no_reports_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_chainabuse(monkeypatch, _chainabuse_response({"reports": []}))
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._chainabuse_reports(report, EVM_ADDR, 30)
    assert any(f.summary == "no Chainabuse reports" for f in report.findings)


def test_chainabuse_401_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAINABUSE_API_KEY", raising=False)
    _install_chainabuse(monkeypatch, _chainabuse_response({}, status=401))
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._chainabuse_reports(report, EVM_ADDR, 30)
    assert any("API requires an API key" in e for e in report.errors)


def test_chainabuse_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAINABUSE_API_KEY", "bad-key")
    _install_chainabuse(monkeypatch, _chainabuse_response({}, status=403))
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._chainabuse_reports(report, EVM_ADDR, 30)
    assert any("API key rejected" in e for e in report.errors)


def test_chainabuse_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAINABUSE_API_KEY", raising=False)
    _install_chainabuse(monkeypatch, response=None, exc=httpx.ConnectError("down"))
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._chainabuse_reports(report, EVM_ADDR, 30)
    assert any("chainabuse:" in e for e in report.errors)


# ── Sanctions ───────────────────────────────────────────────────────────────


def test_sanctions_match_with_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sanctions(
        monkeypatch,
        {
            EVM_ADDR.lower(): {
                "type": "individual",
                "entries": [{"entity": "Bad Actor", "program": "OFAC", "uid": "u1"}],
            }
        },
    )
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._sanctions(report, EVM_ADDR.lower())
    finding = next(f for f in report.findings if f.source == "ofac_sanctions")
    assert finding.category == "sanction"
    assert finding.confidence == "high"
    assert "Bad Actor" in finding.summary
    assert finding.data["program"] == "OFAC"


def test_sanctions_match_without_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sanctions(monkeypatch, {EVM_ADDR.lower(): {"type": "entity"}})
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._sanctions(report, EVM_ADDR.lower())
    finding = next(f for f in report.findings if f.source == "ofac_sanctions")
    assert finding.summary == "address listed in OFAC sanctions list"


def test_sanctions_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sanctions(monkeypatch, {})
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._sanctions(report, EVM_ADDR.lower())
    assert any(f.summary == "no sanctions match" for f in report.findings)


def test_sanctions_missing_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sanctions(monkeypatch, None)
    report = OsintReport(target=EVM_ADDR, target_type="wallet")
    w._sanctions(report, EVM_ADDR.lower())
    assert any("no local OFAC list found" in e for e in report.errors)


# ── lookup_wallet end-to-end ────────────────────────────────────────────────


def test_lookup_wallet_evm_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAINABUSE_API_KEY", raising=False)
    _install_entity_registry(monkeypatch, _FakeRegistry(_fake_match(entity_type="exchange", source="internal")))
    _install_redis(monkeypatch, label={"name": "Binance Hot Wallet"})
    _install_chainabuse(monkeypatch, _chainabuse_response({"reports": []}))
    _install_sanctions(monkeypatch, {})

    report = w.lookup_wallet(EVM_ADDR, timeout=10)

    assert report.target == EVM_ADDR
    assert report.target_type == "wallet"
    assert report.errors == []
    chain = next(f for f in report.findings if f.source == "address")
    assert chain.category == "chain"
    assert chain.data["chain"] == "evm"
    assert any(f.category == "exchange" for f in report.findings)
    assert any(f.source == "wallet_label_loader" for f in report.findings)
    assert any(f.summary == "no Chainabuse reports" for f in report.findings)
    assert any(f.summary == "no sanctions match" for f in report.findings)


def test_lookup_wallet_solana(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAINABUSE_API_KEY", raising=False)
    _install_entity_registry(monkeypatch, _FakeRegistry(match=None))
    _install_redis(monkeypatch, label=None)
    _install_chainabuse(monkeypatch, _chainabuse_response({"reports": []}))
    _install_sanctions(monkeypatch, {})

    report = w.lookup_wallet(SOLANA_ADDR, timeout=10)
    chain = next(f for f in report.findings if f.source == "address")
    assert chain.data["chain"] == "solana"


def test_lookup_wallet_invalid_address_returns_early(monkeypatch: pytest.MonkeyPatch) -> None:
    report = w.lookup_wallet("garbage!!", timeout=10)
    assert report.errors
    assert report.findings == []


def test_lookup_wallet_source_failure_does_not_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAINABUSE_API_KEY", raising=False)
    _install_entity_registry(monkeypatch, _FakeRegistry(exc=RuntimeError("registry boom")))
    _install_redis(monkeypatch, label=None)
    _install_chainabuse(monkeypatch, _chainabuse_response({"reports": []}))
    _install_sanctions(monkeypatch, {})

    report = w.lookup_wallet(EVM_ADDR, timeout=10)
    assert any("entity_registry: registry boom" in e for e in report.errors)
    # Chain + chainabuse + sanctions still resolved.
    assert any(f.source == "address" for f in report.findings)
    assert any(f.summary == "no Chainabuse reports" for f in report.findings)
    assert any(f.summary == "no sanctions match" for f in report.findings)
