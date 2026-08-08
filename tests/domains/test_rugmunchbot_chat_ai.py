"""Unit tests for the rugmunchbot backend RAG chat handler (handlers/chat_ai.py).

Contract under test:
  _extract_addresses    — EVM/Solana address detection in free text (capped,
                          Solana false-positive filtering)
  _format_scan_summary  — compact risk summary line for address questions
  _chunk_text           — Telegram-sized chunking on raw text (pre-escape)
  _is_meaningful_question — gibberish gate (no LLM tokens burned on noise)
  _call_backend_chat    — POST /api/v1/chat payload/headers + X-RAG-Context
                          header parsing; graceful None on failure

No network I/O: httpx is mocked at the module boundary for the async test.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

from app.domains.telegram.rugmunchbot.handlers import chat_ai

if TYPE_CHECKING:
    import pytest

EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _run(coro: object) -> object:
    return asyncio.run(coro)


class TestExtractAddresses:
    def test_evm_in_sentence(self) -> None:
        assert chat_ai._extract_addresses(f"is this token {EVM} a honeypot?") == [EVM]

    def test_solana_in_sentence(self) -> None:
        assert chat_ai._extract_addresses(f"check out {SOL} on jupiter") == [SOL]

    def test_evm_plus_solana(self) -> None:
        found = chat_ai._extract_addresses(f"{EVM} and also {SOL}")
        assert found == [EVM, SOL]

    def test_capped_at_two(self) -> None:
        text = f"{EVM} {SOL} {EVM}"
        assert len(chat_ai._extract_addresses(text)) <= chat_ai.MAX_ADDRESS_HINTS

    def test_evm_wins_over_solana_false_positive(self) -> None:
        # An EVM address is 42 chars — the Solana regex would match the same
        # run (minus the 0x prefix) if not for the overlap filter.
        found = chat_ai._extract_addresses(f"scan {EVM} please")
        assert found == [EVM]

    def test_word_shaped_solana_false_positive_filtered(self) -> None:
        # 34 chars, lowercase only (contains 'o') — matches the raw regex but
        # must be filtered by the digit/uppercase plausibility gate.
        word = "supercalifragilisticexpialidocious"
        assert chat_ai._extract_addresses(f"what does {word} mean") == []

    def test_no_address(self) -> None:
        assert chat_ai._extract_addresses("how do honeypots work") == []

    def test_short_text_no_match(self) -> None:
        assert chat_ai._extract_addresses("hello there") == []


class TestFormatScanSummary:
    def test_full_summary(self) -> None:
        data = {
            "risk_score": 82,
            "is_honeypot": True,
            "is_mintable": False,
            "is_renounced": True,
            "liquidity_usd": 12345.6,
            "holders": 999,
        }
        summary = chat_ai._format_scan_summary(EVM, data)
        assert summary is not None
        assert summary.startswith("Context: token ")
        assert "risk_score=82" in summary
        assert "honeypot=True" in summary
        assert "mintable=False" in summary
        assert "renounced=True" in summary
        assert "liquidity=$12,346" in summary
        assert "holders=999" in summary

    def test_missing_risk_returns_none(self) -> None:
        assert chat_ai._format_scan_summary(EVM, {"error": "no data"}) is None
        assert chat_ai._format_scan_summary(EVM, {}) is None

    def test_sparse_data(self) -> None:
        summary = chat_ai._format_scan_summary(SOL, {"risk_score": 0})
        assert summary is not None
        assert "risk_score=0" in summary
        assert "honeypot=" not in summary


class TestChunkText:
    def test_short_text_unchunked(self) -> None:
        text = "short answer"
        assert chat_ai._chunk_text(text) == [text]

    def test_empty(self) -> None:
        assert chat_ai._chunk_text("") == []

    def test_paragraph_boundaries(self) -> None:
        text = "\n\n".join(f"paragraph {i} " + "x" * 1200 for i in range(4))
        chunks = chat_ai._chunk_text(text, max_len=2500)
        assert len(chunks) > 1
        assert all(len(c) <= 2500 for c in chunks)
        assert "".join(chunks).replace("\n\n", "") == text.replace("\n\n", "")

    def test_oversized_single_paragraph_hard_split(self) -> None:
        paragraph = "word " * 2000  # ~10k chars, one paragraph
        chunks = chat_ai._chunk_text(paragraph, max_len=1000)
        assert len(chunks) > 1
        assert all(len(c) <= 1000 for c in chunks)

    def test_chunks_join_back_to_original_content(self) -> None:
        text = ("a " * 500) + "\n\n" + ("b " * 2000) + "\n\n" + ("c " * 700)
        chunks = chat_ai._chunk_text(text, max_len=1000)

        # The chunker rejoins hard-split pieces with paragraph separators, so
        # whitespace is normalized; verify content (letters) is lossless.
        def letters(s: str) -> list[str]:
            return [ch for ch in s if ch.isalpha()]

        assert letters("".join(chunks)) == letters(text)


class TestIsMeaningfulQuestion:
    def test_normal_question(self) -> None:
        assert chat_ai._is_meaningful_question("how do honeypots work?") is True

    def test_too_short(self) -> None:
        assert chat_ai._is_meaningful_question("hi") is False

    def test_repeated_word_spam(self) -> None:
        assert chat_ai._is_meaningful_question("asdf asdf asdf asdf asdf") is False

    def test_emoji_only(self) -> None:
        assert chat_ai._is_meaningful_question("🔥🔥🔥🔥🔥") is False


class TestCallBackendChat:
    def test_success_parses_message_and_rag_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeResponse:
            status_code = 200
            headers: ClassVar[dict[str, str]] = {"X-RAG-Context": "7"}

            def json(self) -> dict:
                return {"message": "grounded answer", "usage": {"total_tokens": 42}, "model": "m"}

        class _FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
                return _FakeResponse()

        monkeypatch.setattr(chat_ai.httpx, "AsyncClient", _FakeClient)
        result = _run(chat_ai._call_backend_chat("question", 12345))
        assert result is not None
        assert result["message"] == "grounded answer"
        assert result["rag_count"] == 7
        assert result["tokens"] == 42

    def test_non_200_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeResponse:
            status_code = 429
            headers: ClassVar[dict[str, str]] = {}

            def json(self) -> dict:
                return {}

        class _FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
                return _FakeResponse()

        monkeypatch.setattr(chat_ai.httpx, "AsyncClient", _FakeClient)
        assert _run(chat_ai._call_backend_chat("question", 12345)) is None

    def test_network_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, *args: object, **kwargs: object) -> None:
                raise OSError("backend down")

        monkeypatch.setattr(chat_ai.httpx, "AsyncClient", _FakeClient)
        assert _run(chat_ai._call_backend_chat("question", 12345)) is None

    def test_payload_contract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the wire format: no context_override + explicit history."""
        captured: dict = {}

        class _FakeResponse:
            status_code = 200
            headers: ClassVar[dict[str, str]] = {"X-RAG-Context": "0"}

            def json(self) -> dict:
                return {"message": "ok"}

        class _FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, url: str, headers: dict, json: dict) -> _FakeResponse:
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
                return _FakeResponse()

        monkeypatch.setattr(chat_ai.httpx, "AsyncClient", _FakeClient)
        _run(
            chat_ai._call_backend_chat(
                "follow-up question",
                777,
                [{"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"}],
            )
        )
        assert captured["url"] == chat_ai.CHAT_ENDPOINT
        # Per-user session: telegram id forwarded for backend rate limiting.
        assert captured["headers"]["X-Forwarded-For"] == "777"
        # Explicit per-user history overrides the shared backend bucket.
        assert captured["json"]["history"] == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
        ]
        # No context_override -> backend auto-RAG (news-aware grounding).
        assert "context_override" not in captured["json"]
