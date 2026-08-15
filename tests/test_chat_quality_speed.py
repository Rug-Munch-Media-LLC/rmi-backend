"""Pure unit tests for chat quality + speed improvements.

Covers ``app/routers/chat.py``:
  - ``_route_task``: complexity-based model routing (analysis 8B vs chat 4B).
  - ``_cache_key`` / ``_cache_get`` / ``_cache_set``: in-process TTL response
    cache (deterministic keys, expiry, cap eviction).
  - ``_resolve_chat_max_tokens_default``: ``RMI_CHAT_MAX_TOKENS`` env override
    parsing (valid / invalid / clamped).
  - ``ChatRequest.max_tokens`` default is 320 (2048 -> 512 speed pass, then
    512 -> 1536 on 2026-08-14 so long educational answers are not truncated
    mid-sentence; 1536 -> 768 on 2026-08-15 since tool/RAG/web context
    fetches now run in parallel and the tool-result block is capped at 1200
    chars; 768 -> 320 on 2026-08-15 — measured 4B CPU generation 87-136s at
    768 vs 24-27s at 320 with the real ~2100-token prompt — the deploy-time
    env override still clamps to 64..2048).
  - ``SYSTEM_PROMPT`` contains the new cite-or-omit / never-invent rules.

No DB, network, or LLM calls are made — everything tested is a pure function
of the message text (or module-level state cleared between tests).
"""

from __future__ import annotations

import os

# Ensure the ChatRequest default is the base 768 when this module is imported
# (the module reads RMI_CHAT_MAX_TOKENS at import time).
os.environ.pop("RMI_CHAT_MAX_TOKENS", None)

import pytest

from app.routers.chat import (
    SYSTEM_PROMPT,
    ChatRequest,
    _cache_get,
    _cache_key,
    _cache_set,
    _chat_cache,
    _resolve_chat_max_tokens_default,
    _route_task,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset module-level cache between tests (eviction tests are stateful)."""
    _chat_cache.clear()
    yield
    _chat_cache.clear()


# ── 1. Token budget: ChatRequest default + env override ────────────────


def test_chat_request_default_max_tokens_is_320() -> None:
    assert ChatRequest(message="hello").max_tokens == 320


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, 320),  # unset → base default
        ("", 320),
        ("   ", 320),
        ("1024", 1024),  # valid override
        ("2048", 2048),  # at the clamp ceiling
        ("8192", 2048),  # clamped down to 2048
        ("999999", 2048),  # clamped down to 2048
        ("10", 64),  # clamped up to 64
        ("-5", 64),  # clamped up to 64
        ("abc", 320),  # invalid → fall back to base
        ("512.5", 320),  # not an int → fall back to base
    ],
)
def test_resolve_chat_max_tokens_default_parsing(env_value: str | None, expected: int) -> None:
    assert _resolve_chat_max_tokens_default(env_value) == expected


def test_resolve_chat_max_tokens_default_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RMI_CHAT_MAX_TOKENS", "768")
    assert _resolve_chat_max_tokens_default() == 768
    monkeypatch.delenv("RMI_CHAT_MAX_TOKENS")
    assert _resolve_chat_max_tokens_default() == 320
    monkeypatch.setenv("RMI_CHAT_MAX_TOKENS", "not-an-int")
    assert _resolve_chat_max_tokens_default() == 320


# ── 2. SYSTEM_PROMPT quality rules ─────────────────────────────────────


def test_system_prompt_has_cite_or_omit_rule() -> None:
    assert "I don't have that" in SYSTEM_PROMPT
    assert "cite the source" in SYSTEM_PROMPT


def test_system_prompt_has_never_invent_rule() -> None:
    assert "NEVER invent" in SYSTEM_PROMPT
    assert "price, market cap, address, or statistic" in SYSTEM_PROMPT


def test_system_prompt_keeps_identity_and_grounding_rules() -> None:
    assert "RMI Terminal" in SYSTEM_PROMPT
    assert "Rug Munch Intelligence" in SYSTEM_PROMPT
    assert "Risk Score / Key Findings / Verdict" in SYSTEM_PROMPT
    assert "SECURITY DIRECTIVES" in SYSTEM_PROMPT
    assert "2-4 short paragraphs" in SYSTEM_PROMPT


# ── 3. Complexity-based routing ────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "hello",
        "what is bitcoin",
        "what is the price of solana",
        "tell me about ethereum",
        "how do i buy solana",
        "What Is Bitcoin",
        "  what is ethereum  ",
        # Tightened 2026-08-15: risk-rank / market / quick-check asks are fast
        # (grounded by live tool data on the 4B), not deep 9B analysis.
        "what is a rug pull?",  # definitional — fast (was: "rug" term → analysis)
        "check for rug risk on this coin",  # quick security check — fast
        "whale moved 10k eth to an exchange",  # market-flow read — fast
        "riskiest picks of the day",
        "what are the sketchiest tokens on solana with rug potential",
    ],
)
def test_route_task_chat_for_simple_questions(message: str) -> None:
    assert _route_task(message) == "chat"


@pytest.mark.parametrize(
    "message",
    [
        "why did bitcoin crash",
        "explain how uniswap works",
        "how does a rug pull happen",
        "compare bitcoin and ethereum",
        "analyze this token",
        "investigate this wallet",
        "trace the flow of funds from that hack",
        "what happened to $PEPE yesterday",
        "deep dive on solana security",
        "is there MEV on this DEX",
        "was that a known exploit",
        "tell me about on-chain forensics",
        "WHY DID BTC CRASH",
        # Tightened 2026-08-15: genuine contract/forensics asks go deep.
        "is this contract a honeypot",
        "analyze this smart contract",
        "who is the deployer of this token",
        "check the tokenomics before i aped in",
    ],
)
def test_route_task_analysis_for_analytical_questions(message: str) -> None:
    assert _route_task(message) == "analysis"


def test_route_task_long_message_is_analysis() -> None:
    long_msg = (
        "can you give me a detailed summary of how the solana ecosystem has evolved over the last "
        "year including the major protocol upgrades, the memecoin season, and where things stand "
        "today with the newest tokens and liquidity"
    )
    assert len(long_msg) > 120
    assert _route_task(long_msg) == "analysis"


def test_route_task_empty_message_is_chat() -> None:
    assert _route_task("") == "chat"
    assert _route_task("   ") == "chat"


# ── 4. Response cache ──────────────────────────────────────────────────


def test_cache_key_deterministic_and_normalized() -> None:
    assert _cache_key("Hello World") == _cache_key("hello world")
    assert _cache_key("Hello World") == _cache_key("  hello   world  ")
    assert _cache_key("SOL price") == _cache_key("sol PRICE")
    assert _cache_key("hello") != _cache_key("world")
    # Punctuation is content, not whitespace: "price?" != "price".
    assert _cache_key("SOL price?") != _cache_key("SOL price")


def test_cache_set_get_roundtrip() -> None:
    key = _cache_key("what is bitcoin")
    assert _cache_get(key) is None  # miss before set
    _cache_set(key, "Bitcoin is a decentralized currency.", ttl=60)
    assert _cache_get(key) == "Bitcoin is a decentralized currency."


def test_cache_get_unknown_key_returns_none() -> None:
    assert _cache_get("does-not-exist") is None


def test_cache_expires_after_ttl() -> None:
    key = _cache_key("what is solana")
    _cache_set(key, "Solana is a fast blockchain.", ttl=-1)  # already expired
    assert _cache_get(key) is None
    assert key not in _chat_cache  # expired entry was evicted on read


def test_cache_set_overwrites_and_refreshes_ttl() -> None:
    key = _cache_key("price check")
    _cache_set(key, "first", ttl=60)
    _cache_set(key, "second", ttl=60)
    assert _cache_get(key) == "second"


def test_cache_evicts_oldest_at_cap() -> None:
    for i in range(257):  # one past the 256-entry cap
        _cache_set(f"key-{i:03d}", f"value-{i}", ttl=3600)
    assert len(_chat_cache) <= 256
    # The first-inserted (oldest) entry was evicted; the newest survived.
    assert _cache_get("key-000") is None
    assert _cache_get("key-256") == "value-256"
