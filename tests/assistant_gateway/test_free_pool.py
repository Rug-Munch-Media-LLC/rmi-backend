"""Free pool contract: order, quota, breaker, fail-closed, no paid/local lanes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.assistant_gateway.free_pool.select import FreePoolExhausted, pick_model
from app.assistant_gateway.free_pool.spec import PoolSpec, ProviderSpec, load_pool
from app.assistant_gateway.free_pool.state import PoolState

SPEC_PATH = Path(__file__).resolve().parents[2] / "app" / "assistant_gateway" / "free_pool" / "pool.json"
BANNED_HOSTS = ("127.0.0.1", "localhost", ":8085", ":8086", ":20199", ":20201", ":18091", ":18092", ":18093")
BANNED_MODELS = ("fleet-free", "fleet-hf", "fleet-text", "hf-", "qwen-chat", "qwen-analysis", "deepseek-v4-flash")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _pool(*ids: str, **overrides: Any) -> PoolSpec:
    providers = tuple(
        ProviderSpec(
            id=pid,
            model=f"model-{pid}",
            base_url="https://example.invalid/v1",
            secret_env=f"{pid.upper()}_KEY",
            **overrides,
        )
        for pid in ids
    )
    return PoolSpec(name="chat-free", providers=providers)


def test_pool_file_is_valid_json_and_ordered() -> None:
    spec = load_pool(SPEC_PATH)
    assert spec.name == "chat-free"
    assert spec.fail_closed_code == "free_pool_exhausted"
    enabled = [p.id for p in spec.enabled()]
    assert enabled[0] == "openrouter-main"
    assert enabled[1] == "openrouter-backup"
    assert enabled.index("groq-gpt-oss-120b") < enabled.index("gemini-flash-lite")


def test_no_local_hf_or_paid_lane_can_enter_the_pool() -> None:
    raw = json.loads(SPEC_PATH.read_text())
    for provider in raw["providers"]:
        blob = f"{provider['base_url']} {provider['model']} {provider['id']}"
        for banned in BANNED_HOSTS + BANNED_MODELS:
            assert banned not in blob, f"{provider['id']} references banned lane {banned}"


def test_pick_model_returns_first_healthy_in_order() -> None:
    spec = _pool("a", "b")
    state = PoolState(spec, clock=Clock())
    assert pick_model(spec, state).id == "a"
    state.record("a", ok=False, error="500")
    assert pick_model(spec, state).id == "a"  # one failure is tolerated
    state.record("a", ok=False, error="500")
    assert pick_model(spec, state).id == "b"  # breaker opened after 2


def test_breaker_half_opens_after_reset_window() -> None:
    clock = Clock()
    spec = _pool("a")
    state = PoolState(spec, clock=clock)
    state.record("a", ok=False)
    state.record("a", ok=False)
    assert state.snapshot()["a"]["breaker"] == "open"
    clock.now += 61
    assert state.snapshot()["a"]["breaker"] == "open"  # state label until success
    assert [p.id for p in state.candidates()] == ["a"]  # probe allowed again


def test_daily_quota_exhausts_a_lane() -> None:
    spec = _pool("a", daily_requests=2)
    state = PoolState(spec, clock=Clock())
    state.record("a", ok=True)
    state.record("a", ok=True)
    assert state.candidates() == []


def test_fail_closed_reports_free_pool_exhausted_and_never_falls_back() -> None:
    spec = _pool("a", "b")
    state = PoolState(spec, clock=Clock())
    for pid in ("a", "b"):
        state.record(pid, ok=False, error="429 rate limit")
        state.record(pid, ok=False, error="429 rate limit")
    with pytest.raises(FreePoolExhausted) as excinfo:
        pick_model(spec, state)
    error = excinfo.value.as_error()
    assert error["error"] == "free_pool_exhausted"
    assert error["fallback"] is None
    assert "a" in error["providers"]


def test_disabled_lanes_are_never_selected() -> None:
    spec = PoolSpec(
        name="chat-free",
        providers=(
            ProviderSpec(id="off", model="m", base_url="https://x.invalid", secret_env="X", enabled=False),
            ProviderSpec(id="on", model="m", base_url="https://x.invalid", secret_env="Y"),
        ),
    )
    state = PoolState(spec, clock=Clock())
    assert [p.id for p in spec.enabled()] == ["on"]
    assert pick_model(spec, state).id == "on"
