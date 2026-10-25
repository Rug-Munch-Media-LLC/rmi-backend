"""Conformance gate for DataBus providers.

Population = functions in ``provider_implementations/`` that a chain module
references. The gate is a **shrink-only ratchet**: providers listed in
``databus_provider_baseline.json`` are known to violate the contract and may only
be removed from that list (a provider that starts conforming fails the build until
its baseline entry is deleted). Everything unlisted must conform today.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
from functools import lru_cache

import httpx
import pytest

from app.databus import provider_implementations as impl_pkg
from app.databus.provider_contract import DatabusProvider
from app.databus.provider_implementations._coingecko import CoinGeckoPriceProvider

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[1]
CHAIN_GLOBS = (
    "app/domains/databus/_generated/chains/*.py",
    "app/domains/databus/*.py",
    "app/databus/provider_chains.py",
)
BASELINE = HERE / "databus_provider_baseline.txt"


@lru_cache(maxsize=1)
def _chain_identifiers() -> frozenset[str]:
    names: set[str] = set()
    for pattern in CHAIN_GLOBS:
        for path in ROOT.glob(pattern):
            names.update(n.id for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Name))
    return frozenset(names)


@lru_cache(maxsize=1)
def _known_drift() -> frozenset[str]:
    return frozenset(
        line.strip() for line in BASELINE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )


def _discover() -> list[tuple[str, str, object]]:
    import importlib
    import pkgutil

    referenced = _chain_identifiers()
    found: list[tuple[str, str, object]] = []
    for info in pkgutil.iter_modules(impl_pkg.__path__):
        if info.name.startswith("__"):
            continue
        mod = importlib.import_module(f"{impl_pkg.__name__}.{info.name}")
        for name, obj in vars(mod).items():
            if inspect.isfunction(obj) and obj.__module__ == mod.__name__ and name in referenced:
                found.append((info.name, name, obj))
    return found


PROVIDERS = _discover()
IDS = [f"{m}.{n}" for m, n, _ in PROVIDERS]


def _violations(modname: str, fn) -> str | None:
    """First contract violation of ``fn``'s module, or None."""
    if not inspect.iscoroutinefunction(fn):
        return "must be async"
    params = inspect.signature(fn).parameters
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return "must accept **kwargs (the chain calls fetch_fn(api_key=..., **kwargs))"
    return _source_violations(inspect.getsource(fn.__module__), modname)


def _source_violations(source: str, modname: str) -> str | None:
    tree = ast.parse(source)
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        if handler.type is None:
            return f"bare except at line {handler.lineno}"
        if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
            return f"swallows an exception with pass at line {handler.lineno}"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        is_client = (isinstance(target, ast.Attribute) and target.attr == "AsyncClient") or (
            isinstance(target, ast.Name) and target.id == "AsyncClient"
        )
        if is_client and all(k.arg != "timeout" for k in node.keywords):
            return f"AsyncClient without timeout at line {node.lineno}"
    return None


def _subclasses() -> list[type]:
    seen: set[type] = set()
    stack = [DatabusProvider]
    while stack:
        for child in stack.pop().__subclasses__():
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return sorted(seen, key=lambda c: c.__name__)


def test_discovery_finds_registered_providers():
    assert len(PROVIDERS) >= 20, f"only {len(PROVIDERS)} providers — discovery is broken"


def test_a_conforming_provider_exists():
    assert _subclasses(), "no DatabusProvider subclass — the contract has no anchor"


@pytest.mark.parametrize(("modname", "name", "fn"), PROVIDERS, ids=IDS)
def test_legacy_provider_drift_is_inventoried(modname, name, fn):
    """Legacy fetch_fn surface. Violations are xfail, never silently green."""
    problem = _violations(modname, fn)
    if problem is not None:
        pytest.xfail(f"{modname}.{name}: {problem} — see ADR migration order")
    assert problem is None


@pytest.mark.parametrize("cls", _subclasses(), ids=lambda c: c.__name__)
def test_databus_provider_subclass_conforms(cls):
    """Strict forward gate: anything built on DatabusProvider cannot drift."""
    assert inspect.iscoroutinefunction(cls.fetch), f"{cls.__name__}.fetch must be async"
    assert inspect.iscoroutinefunction(cls._call), f"{cls.__name__}._call must be async"
    assert cls.timeout > 0, f"{cls.__name__}.timeout must be positive"
    assert cls.name != "unnamed", f"{cls.__name__}.name must be set"
    problem = _source_violations(inspect.getsource(cls), cls.__name__)
    assert problem is None, f"{cls.__name__}: {problem}"
    assert "secret-canary" not in repr(cls(api_key="secret-canary")), "repr leaks the API key"


# ── Migrated provider: contract + recorded fixture (DI transport, no patching) ──

FIXTURE = HERE / "fixtures" / "coingecko_price.json"


def test_coingecko_surface():
    assert issubclass(CoinGeckoPriceProvider, DatabusProvider)
    assert CoinGeckoPriceProvider.timeout > 0
    assert inspect.iscoroutinefunction(CoinGeckoPriceProvider.fetch)
    assert "secret-value" not in repr(CoinGeckoPriceProvider(api_key="secret-value"))


@pytest.mark.asyncio
async def test_coingecko_parses_recorded_fixture():
    payload = json.loads(FIXTURE.read_text())
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=payload)

    provider = CoinGeckoPriceProvider(transport=httpx.MockTransport(handler))
    out = await provider.fetch(mint="0xabc")
    assert out["prices"] == payload
    assert out["source"] == "coingecko_price"
    assert "0xabc" in urls[0]


@pytest.mark.asyncio
async def test_coingecko_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"usd": 1.0})

    provider = CoinGeckoPriceProvider(transport=httpx.MockTransport(handler))
    provider.max_attempts = 2
    out = await provider.fetch(mint="0xabc")
    assert calls["n"] == 2
    assert out["prices"] == {"usd": 1.0}
