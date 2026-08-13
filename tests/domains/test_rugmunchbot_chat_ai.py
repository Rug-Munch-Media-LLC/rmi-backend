"""Unit tests for the rugmunchbot backend RAG chat handler (handlers/chat_ai.py).

Contract under test:
  _extract_addresses    — EVM/Solana address detection in free text (capped,
                          Solana false-positive filtering)
  _format_scan_summary  — compact risk summary line for address questions
  _chunk_text           — Telegram-sized chunking on raw text (pre-escape)
  _is_meaningful_question — gibberish gate (no LLM tokens burned on noise)
  _call_backend_chat    — POST /api/v1/chat payload/headers + X-RAG-Context
                          header parsing; graceful None on failure
  local_ai_response     — tier-2 canned intent replies (ported from
                          handlers/message.py)
  _lookup_token_symbol  — $TOKEN instant DexScreener price card (ported from
                          handlers/message.py)
  _route_structural     — free-text feature routing: known-scam block,
                          bare-address auto-scan, $TOKEN card
  chat_ai_response      — shared AI core (quota + flood + gibberish redirect +
                          backend RAG chat), used by free text, /chat, /ask
  ai_chat_handler       — single free-text pipeline entry (merged)

No network I/O: httpx is mocked at the module boundary for the async test.
The bot SQLite DB is redirected to a temp file per test so the real
bot_users.db is never touched.
"""

from __future__ import annotations

import asyncio
import types
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers import chat_ai

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

    def test_section_headers_lead_chunks(self) -> None:
        """v10: emoji section headers start a fresh chunk — never dangling at
        the tail of the previous section."""
        text = "\n\n".join(
            f"**{emoji} {title}**\n{body}"
            for emoji, title, body in [
                ("\U0001f4ca", "Market Read", "content " * 200),
                ("\U0001f50d", "On-Chain", "content " * 200),
                ("\U0001f40b", "Whales", "content " * 200),
            ]
        )
        chunks = chat_ai._chunk_text(text, max_len=2000)
        assert len(chunks) > 1
        assert all(len(c) <= 2000 for c in chunks)
        # Every chunk that is not the first must open with a section header.
        for chunk in chunks[1:]:
            assert chunk.lstrip().startswith("**")
        # Every section header must be present at the start of some chunk.
        heads = [c.lstrip()[:12] for c in chunks]
        assert any(h.startswith("**\U0001f4ca") for h in heads)
        assert any(h.startswith("**\U0001f50d") for h in heads)
        assert any(h.startswith("**\U0001f40b") for h in heads)

    def test_chunks_join_back_to_original_content(self) -> None:
        text = ("a " * 500) + "\n\n" + ("b " * 2000) + "\n\n" + ("c " * 700)
        chunks = chat_ai._chunk_text(text, max_len=1000)

        # The chunker rejoins hard-split pieces with paragraph separators, so
        # whitespace is normalized; verify content (letters) is lossless.
        def letters(s: str) -> list[str]:
            return [ch for ch in s if ch.isalpha()]

        assert letters("".join(chunks)) == letters(text)


class TestSplitOversized:
    def test_sentence_boundary_preferred(self) -> None:
        """v10: hard splits land after sentence punctuation, not mid-sentence."""
        paragraph = ("This is sentence one. " * 60) + "tail"
        parts = chat_ai._split_oversized(paragraph, 1000)
        assert len(parts) > 1
        assert all(len(p) <= 1000 for p in parts)
        # First part must end on a complete sentence.
        assert parts[0].rstrip().endswith("one.")

    def test_word_boundary_fallback(self) -> None:
        paragraph = "word " * 3000  # no sentence punctuation, word boundaries only
        parts = chat_ai._split_oversized(paragraph, 1000)
        assert len(parts) > 1
        assert all(len(p) <= 1000 for p in parts)
        assert all(p.split() for p in parts)  # no mid-word cuts


class TestBeautifyAnswer:
    def test_plain_header_gets_bold_and_separator(self) -> None:
        raw = chat_ai._beautify_answer("\U0001f4ca Market Read\nSome evidence here.")
        lines = raw.split("\n")
        assert lines[0] == "**\U0001f4ca Market Read**"
        assert lines[1] == chat_ai.thin_sep()
        assert lines[2] == "Some evidence here."

    def test_already_bold_header_not_double_wrapped(self) -> None:
        raw = chat_ai._beautify_answer("**\U0001f50d On-Chain Check**\nBody.")
        lines = raw.split("\n")
        assert lines[0] == "**\U0001f50d On-Chain Check**"
        assert lines[1] == chat_ai.thin_sep()

    def test_blank_line_inserted_before_header(self) -> None:
        raw = chat_ai._beautify_answer("First section content.\n\n\U0001f40b Whales\nWhale body.")
        # Header is preceded by a blank line so the chunker sees a boundary.
        assert "content.\n\n**\U0001f40b Whales**" in raw
        assert "**\U0001f40b Whales**\n" in raw

    def test_long_emoji_line_is_not_a_header(self) -> None:
        prose = (
            "\U0001f4c8 This token's chart broke out of a massive falling wedge "
            "that has been building since March and the volume profile suggests "
            "a real accumulation phase behind it."
        )
        assert chat_ai._beautify_answer(prose) == prose

    def test_non_header_lines_preserved_verbatim(self) -> None:
        code = "    if x > 1:\n        return x"  # indentation must survive
        raw = chat_ai._beautify_answer(f"{code}\n\nplain text")
        assert code in raw
        assert "plain text" in raw

    def test_bold_metric_line_untouched(self) -> None:
        raw = chat_ai._beautify_answer("**\U0001f7e2 SAFE \u2014 76/100**\n")
        assert raw == "**\U0001f7e2 SAFE \u2014 76/100**"

    def test_is_section_header_heuristics(self) -> None:
        assert chat_ai._is_section_header("\U0001f4ca Market Read")
        assert chat_ai._is_section_header("**\U0001f6e1\ufe0f Security**")
        assert not chat_ai._is_section_header("not a header")
        assert not chat_ai._is_section_header("")


class TestMarkdownBoldToHtml:
    def test_bold_converted(self) -> None:
        assert chat_ai._markdown_bold_to_html("**bold**") == "<b>bold</b>"

    def test_triple_asterisk_converted(self) -> None:
        assert chat_ai._markdown_bold_to_html("***bold***") == "<b>bold</b>"

    def test_escaped_content_stays_safe(self) -> None:
        # Runs AFTER html.escape: angle brackets inside bold are entities, and
        # the conversion must NOT re-introduce raw HTML.
        text = "<b>&lt;script&gt;alert(1)&lt;/script&gt;</b>"
        assert "<script>alert(1)</script>" not in chat_ai._markdown_bold_to_html(text)

    def test_unbalanced_asterisks_untouched(self) -> None:
        assert chat_ai._markdown_bold_to_html("*stray") == "*stray"
        assert chat_ai._markdown_bold_to_html("**open only") == "**open only"


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


# ══════════════════════════════════════════════════════════════════════════
# Merged-pipeline tests (ported from handlers/message.py + chat_ai core)
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the bot DB at a temp file so tests never touch the real DB."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bot_test.db"))
    db.init_db()


class _FakeChat:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def send_action(self, action: str) -> None:
        self.actions.append(action)


class _FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.chat = _FakeChat()
        self.text = text
        self.replies: list[tuple[tuple, dict]] = []
        self.returned: list[object] = []

    async def reply_text(self, *args: object, **kwargs: object) -> object:
        self.replies.append((args, kwargs))
        ph = types.SimpleNamespace()
        ph.edits: list[tuple[tuple, dict]] = []

        async def edit_text(*a: object, **k: object) -> None:
            ph.edits.append((a, k))

        ph.edit_text = edit_text
        self.returned.append(ph)
        return ph


class _FakeUpdate:
    def __init__(self, user_id: int, username: str, message: object) -> None:
        self.effective_user = types.SimpleNamespace(id=user_id, username=username, first_name="Tester")
        self.message = message


class _FakeDexClient:
    """httpx.AsyncClient class fake returning a configurable DexScreener search.

    Configuration is set on the class attributes (``_status`` / ``_pairs``)
    before monkeypatching ``chat_ai.httpx.AsyncClient`` with this class — the
    handler instantiates it with ``timeout=...``, which the constructor
    accepts and ignores.
    """

    _status: ClassVar[int] = 200
    _pairs: ClassVar[list[dict]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeDexClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str) -> object:
        class _Resp:
            status_code = _FakeDexClient._status

            def json(self) -> dict:
                return {"pairs": list(_FakeDexClient._pairs)}

        return _Resp()


class _FakeDexErrorClient:
    """httpx.AsyncClient fake that raises on GET (DexScreener down)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeDexErrorClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str) -> object:
        raise OSError("dex down")


def _patch_dex(monkeypatch: pytest.MonkeyPatch, *, status: int = 200, pairs: list[dict] | None = None) -> None:
    """Point ``chat_ai.httpx.AsyncClient`` at the configurable fake class."""
    _FakeDexClient._status = status
    _FakeDexClient._pairs = pairs if pairs is not None else []
    monkeypatch.setattr(chat_ai.httpx, "AsyncClient", _FakeDexClient)


class TestLocalIntent:
    def test_greeting(self) -> None:
        assert "Yo! I'm here" in (chat_ai.local_ai_response("hi there", None) or "")

    def test_help(self) -> None:
        assert "RugMunch Capabilities" in (chat_ai.local_ai_response("what can you do", None) or "")

    def test_price_intent(self) -> None:
        assert "Price & Market Data" in (chat_ai.local_ai_response("show me the price of btc", None) or "")

    def test_scan_intent(self) -> None:
        assert "Token Security Scan" in (chat_ai.local_ai_response("is this token safe", None) or "")

    def test_no_match(self) -> None:
        assert chat_ai.local_ai_response("explain yield farming", None) is None


class TestLookupTokenSymbol:
    def test_no_symbol_no_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dex(monkeypatch)
        msg = _FakeMessage()
        assert _run(chat_ai._lookup_token_symbol("hello there", msg)) is False
        assert msg.replies == []

    def test_symbol_success_replies_price_card(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dex(
            monkeypatch,
            pairs=[
                {
                    "baseToken": {"address": "0xabc", "name": "Doge Coin"},
                    "chainId": "ethereum",
                    "priceUsd": "0.1234",
                    "priceChange": {"h24": 5.5},
                    "volume": {"h24": 1000000},
                    "marketCap": 50000000,
                }
            ],
        )
        msg = _FakeMessage()
        result = _run(chat_ai._lookup_token_symbol("check $DOGE", msg))
        assert result is True
        assert len(msg.replies) == 1
        text = msg.replies[0][0][0]
        assert "DOGE" in text
        assert "MCap" in text
        kb = msg.replies[0][1]["reply_markup"]
        assert "scan_0xabc_ethereum" in kb.to_json()

    def test_symbol_no_pairs_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dex(monkeypatch, status=200, pairs=[])
        msg = _FakeMessage()
        assert _run(chat_ai._lookup_token_symbol("$DOGE", msg)) is False

    def test_symbol_http_error_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(chat_ai.httpx, "AsyncClient", _FakeDexErrorClient)
        msg = _FakeMessage()
        assert _run(chat_ai._lookup_token_symbol("$DOGE", msg)) is False
        assert msg.replies == []


class TestRouteStructural:
    def test_known_scam_address_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(chat_ai.anti_abuse, "is_known_scam_address", lambda addr: True)
        msg = _FakeMessage()
        upd = _FakeUpdate(123, "tester", msg)
        ctx = types.SimpleNamespace(args=[])
        result = _run(chat_ai._route_structural(upd, ctx, f"scan {EVM}", upd.effective_user))
        assert result is True
        assert len(msg.replies) == 1
        assert "Known Scam Address" in msg.replies[0][0][0]

    def test_bare_address_routes_to_scan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list = []

        async def fake_cmd_scan(update: object, ctx: object) -> None:
            calls.append((update, ctx))

        import app.domains.telegram.rugmunchbot.handlers.scanning as scanning_mod

        monkeypatch.setattr(scanning_mod, "cmd_scan", fake_cmd_scan)
        monkeypatch.setattr(chat_ai, "is_evm", lambda text: text == EVM)
        monkeypatch.setattr(chat_ai, "is_sol", lambda text: False)
        msg = _FakeMessage()
        upd = _FakeUpdate(123, "tester", msg)
        ctx = types.SimpleNamespace(args=[])
        result = _run(chat_ai._route_structural(upd, ctx, EVM, upd.effective_user))
        assert result is True
        assert len(calls) == 1
        assert ctx.args == [EVM]

    def test_token_symbol_routes_to_price_card(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dex(
            monkeypatch,
            pairs=[
                {
                    "baseToken": {"address": "0xabc", "name": "Doge Coin"},
                    "chainId": "ethereum",
                    "priceUsd": "0.1234",
                    "priceChange": {"h24": 0},
                    "volume": {"h24": 0},
                    "marketCap": 0,
                }
            ],
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(123, "tester", msg)
        ctx = types.SimpleNamespace(args=[])
        result = _run(chat_ai._route_structural(upd, ctx, "what is $DOGE", upd.effective_user))
        assert result is True
        assert len(msg.replies) == 1

    def test_plain_question_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dex(monkeypatch, status=200, pairs=[])
        msg = _FakeMessage()
        upd = _FakeUpdate(123, "tester", msg)
        ctx = types.SimpleNamespace(args=[])
        result = _run(chat_ai._route_structural(upd, ctx, "how do honeypots work", upd.effective_user))
        assert result is False
        assert msg.replies == []


class TestChatAiResponse:
    @pytest.fixture(autouse=True)
    def _patch_boundaries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(chat_ai, "check_spam", lambda uid: False)
        monkeypatch.setattr(chat_ai, "check_flood", AsyncMock(return_value=(True, 0, None)))
        monkeypatch.setattr(chat_ai, "refund_flood", AsyncMock())

    def test_success_backend_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(return_value={"message": "grounded answer", "rag_count": 2, "tokens": 10}),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "how do honeypots work?", upd.effective_user))
        assert result is True
        assert msg.chat.actions == ["typing"]
        answer_reply = msg.replies[-1][0][0]
        assert "grounded answer" in answer_reply
        # v10 footer: "⚡ Grounded in {N} intelligence sources | RugMunch Bot"
        assert "Grounded in 2 intelligence sources | RugMunch Bot" in answer_reply

    def test_gibberish_redirect_no_backend_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = AsyncMock(return_value={"message": "should not be called", "rag_count": 0})
        monkeypatch.setattr(chat_ai, "_call_backend_chat", backend)
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "asdf asdf asdf asdf asdf", upd.effective_user))
        assert result is True
        backend.assert_not_called()
        assert "RugMunch AI" in msg.replies[0][0][0]
        chat_ai.refund_flood.assert_awaited_once()

    def test_backend_down_local_intent_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(chat_ai, "_call_backend_chat", AsyncMock(return_value=None))
        # Degradation test: the whole LLM chain is down, so the tier-2 canned
        # intent reply must take over. Zen/local are explicitly dead here —
        # otherwise a live llama.cpp server would answer and the test would
        # be environment-dependent.
        monkeypatch.setattr(chat_ai, "_call_zen_llm", AsyncMock(return_value=None))
        monkeypatch.setattr(chat_ai, "_call_local_llm", AsyncMock(return_value=None))
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "what is a honeypot?", upd.effective_user))
        assert result is True
        assert "Token Security Scan" in msg.replies[-1][0][0]

    def test_backend_down_fallback_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(chat_ai, "_call_backend_chat", AsyncMock(return_value=None))
        # Same as above: force the Zen/local fallbacks to fail so the honest
        # unavailability message is edited into the placeholder.
        monkeypatch.setattr(chat_ai, "_call_zen_llm", AsyncMock(return_value=None))
        monkeypatch.setattr(chat_ai, "_call_local_llm", AsyncMock(return_value=None))
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "explain yield farming", upd.effective_user))
        assert result is True
        # The "Thinking..." placeholder is created first, then edited to the
        # honest unavailability message when the backend is down.
        assert msg.replies[0][0][0].startswith("\U0001f916")
        assert len(msg.returned) == 1
        placeholder = msg.returned[0]
        assert len(placeholder.edits) == 1
        assert "AI brain temporarily unavailable" in placeholder.edits[0][0][0]


class TestAiChatHandler:
    @pytest.fixture(autouse=True)
    def _patch_boundaries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(chat_ai, "check_spam", lambda uid: False)
        monkeypatch.setattr(chat_ai, "check_flood", AsyncMock(return_value=(True, 0, None)))
        monkeypatch.setattr(chat_ai, "refund_flood", AsyncMock())

    def test_free_text_flows_to_backend_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(return_value={"message": "rag answer", "rag_count": 1, "tokens": 10}),
        )
        msg = _FakeMessage(text="how do honeypots work?")
        upd = _FakeUpdate(12345, "tester", msg)
        upd.effective_chat = types.SimpleNamespace(id=999, type="private")
        ctx = types.SimpleNamespace(args=[])
        _run(chat_ai.ai_chat_handler(upd, ctx))
        assert any("rag answer" in r[0][0] for r in msg.replies)

    def test_token_symbol_short_circuits_ai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = AsyncMock(return_value={"message": "nope", "rag_count": 0})
        monkeypatch.setattr(chat_ai, "_call_backend_chat", backend)
        _patch_dex(
            monkeypatch,
            pairs=[
                {
                    "baseToken": {"address": "0xabc", "name": "Doge Coin"},
                    "chainId": "ethereum",
                    "priceUsd": "0.1234",
                    "priceChange": {"h24": 0},
                    "volume": {"h24": 0},
                    "marketCap": 0,
                }
            ],
        )
        msg = _FakeMessage(text="$DOGE pump?")
        upd = _FakeUpdate(12345, "tester", msg)
        upd.effective_chat = types.SimpleNamespace(id=999, type="private")
        ctx = types.SimpleNamespace(args=[])
        _run(chat_ai.ai_chat_handler(upd, ctx))
        backend.assert_not_called()
        assert any("DOGE" in r[0][0] for r in msg.replies)
