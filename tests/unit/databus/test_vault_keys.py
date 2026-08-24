"""DataBus Vault — ManagedKey lifecycle + pool rotation contracts.

The vault guards every provider key we own (20 keys / 16 providers).
These tests pin the safety-critical behaviors:

* obfuscation: key value never stored as plain str; repr() leaks nothing
* acquire/release lifecycle: success resets 429 streak; rate-limit
  backoff is exponential (capped at 300s); errors >10 → ERROR state
* availability: rate-limited keys recover after backoff; quota-exhausted
  keys are unavailable; disabled keys never return
* pool rotation: least-recently-used acquisition across multiple keys,
  skipping unavailable ones
* status(): NEVER includes the key value
"""

from __future__ import annotations

import time

import pytest

from app.databus.vault import KeyState, ManagedKey, VaultKeyPool


def _key(name="K1", value="secret-value-123", **kw):
    k = ManagedKey(provider="test", key_name=name, _obfuscated=b"", source="vault", **kw)
    k._set_value(value)  # proper storage path (obfuscation happens here)
    return k


# ── Obfuscation ───────────────────────────────────────────────────────


def test_value_never_plain_in_memory():
    k = _key(value="super-secret-key")
    # stored form is reversed bytes, not the plaintext
    assert b"super-secret-key" not in k._obfuscated
    assert k._obfuscated.decode()[::-1] == "super-secret-key"  # round-trips


def test_repr_leaks_nothing():
    k = _key(value="super-secret-key-xyz")
    for surface in (repr(k), str(k.__dict__)):
        assert "super-secret-key-xyz" not in surface


def test_status_never_includes_value():
    k = _key(value="topsecret")
    k.acquire()
    st = k.status()
    assert "topsecret" not in str(st)
    assert "value" not in st


# ── Lifecycle ─────────────────────────────────────────────────────────


def test_acquire_counts_and_returns_value():
    k = _key()
    assert k.acquire() == "secret-value-123"
    assert k.calls_total == 1 and k.calls_this_month == 1


def test_release_success_resets_429_streak():
    k = _key()
    k.release_rate_limited()
    assert k.state == KeyState.RATE_LIMITED
    k.release_success()
    assert k.state == KeyState.ACTIVE
    assert k.consecutive_429s == 0


def test_rate_limit_backoff_exponential_capped():
    k = _key()
    gaps = []
    for _ in range(8):
        k.release_rate_limited(backoff_base=5.0)
        remaining = k.rate_limited_until - time.monotonic()
        gaps.append(remaining)
        # simulate time passing beyond backoff so next release counts fresh
        k.rate_limited_until = time.monotonic() - 1
        k.state = KeyState.ACTIVE
    # exponential growth then capped at 300
    assert gaps[1] > gaps[0]
    assert max(gaps) <= 300.0
    assert gaps[-1] == pytest.approx(300.0, abs=5)


def test_error_state_after_repeated_errors():
    k = _key()
    for _ in range(10):
        k.release_error("boom")
    assert k.state != KeyState.ERROR  # exactly 10 → not yet
    k.release_error("boom")
    assert k.state == KeyState.ERROR


# ── Availability ──────────────────────────────────────────────────────


def test_rate_limited_key_recovers_after_backoff():
    k = _key()
    k.release_rate_limited()
    assert k.is_available() is False
    k.rate_limited_until = time.monotonic() - 1  # backoff elapsed
    assert k.is_available() is True
    assert k.state == KeyState.ACTIVE


def test_quota_exhausted_unavailable():
    k = _key(monthly_quota=100)
    k.calls_this_month = 100
    assert k.is_available() is False
    k.calls_this_month = 99
    assert k.is_available() is True


def test_disabled_never_available():
    k = _key()
    k.state = KeyState.DISABLED
    assert k.is_available() is False


# ── Pool rotation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pool_rotates_across_keys():
    pool = VaultKeyPool(provider="test", rate_rps=100, burst=100)
    pool.add_key("KEY_A", "value-a")
    pool.add_key("KEY_B", "value-b")
    got = []
    for _ in range(4):
        mk = await pool.acquire()
        assert mk is not None
        got.append(mk.key_name)
        mk.release_success()
    # both keys participate (rotation, not stickiness)
    assert set(got) == {"KEY_A", "KEY_B"}


@pytest.mark.asyncio
async def test_pool_skips_rate_limited_key():
    pool = VaultKeyPool(provider="test", rate_rps=100, burst=100)
    pool.add_key("KEY_A", "value-a")
    pool.add_key("KEY_B", "value-b")
    a = await pool.acquire()
    a.release_rate_limited(backoff_base=60)  # long backoff
    b = await pool.acquire()
    assert b.key_name != a.key_name or b is not a


@pytest.mark.asyncio
async def test_pool_returns_none_when_all_exhausted():
    pool = VaultKeyPool(provider="test", rate_rps=100, burst=100)
    pool.add_key("ONLY", "value")
    mk = await pool.acquire()
    mk.monthly_quota = 10
    mk.calls_this_month = 10  # exhaust
    assert await pool.acquire() is None
