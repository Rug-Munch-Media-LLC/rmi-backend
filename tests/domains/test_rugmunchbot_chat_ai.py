"""Unit tests for the rugmunchbot backend RAG chat handler (handlers/chat_ai.py).

Contract under test:
  _extract_addresses    — EVM/Solana address detection in free text (capped,
                          Solana false-positive filtering)
  _format_scan_summary  — compact risk summary line for address questions
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
from app.domains.telegram.rugmunchbot.handlers.chat_ai import _guards
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

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

    def test_model_separator_not_duplicated(self) -> None:
        # v12: the model emits its own ──── under a header; our inserted
        # thin_sep must not stack a second dashed line.
        raw = chat_ai._beautify_answer("\U0001f4ca Market Read\n\u2500\u2500\u2500\u2500\nEvidence.")
        lines = raw.split("\n")
        assert lines[0] == "**\U0001f4ca Market Read**"
        assert lines[1] == chat_ai.thin_sep()
        assert lines[2] == "Evidence."
        sep_lines = [line for line in lines if line.strip() and set(line.strip()) == {"\u2500"}]
        assert len(sep_lines) == 1, "model separator must be deduped, not stacked"

    def test_leading_emoji_run_collapsed(self) -> None:
        # v12: repeated identical emojis at the start of a header collapse
        # (no emoji spam) while the VS16 compound 🛡️ stays intact.
        raw = chat_ai._beautify_answer("\U0001f4ca\U0001f4ca\U0001f4ca Market Read\nBody.")
        assert raw.startswith("**\U0001f4ca Market Read**")
        shield = chat_ai._beautify_answer("\U0001f6e1\ufe0f\U0001f6e1\ufe0f Security\nBody.")
        assert "**\U0001f6e1\ufe0f Security**" in shield

    def test_header_casing_normalized(self) -> None:
        # v12: lowercase header first word is capitalized; ALL-CAPS prose is
        # title-cased; real abbreviations (TA) survive untouched.
        raw = chat_ai._beautify_answer("\U0001f4ca market read\nBody.")
        assert raw.startswith("**\U0001f4ca Market read**")
        caps = chat_ai._beautify_answer("\U0001f4ca MARKET READ\nBody.")
        assert caps.startswith("**\U0001f4ca Market Read**")
        abbr = chat_ai._beautify_answer("\U0001f4ca TA\nBody.")
        assert abbr.startswith("**\U0001f4ca TA**")

    def test_leading_blank_lines_stripped(self) -> None:
        # v12: a leading blank line (model padding) is trimmed.
        raw = chat_ai._beautify_answer("\n\n\U0001f4ca Market Read\nBody.")
        assert not raw.startswith("\n")
        assert raw.startswith("**\U0001f4ca Market Read**")

    def test_is_section_header_heuristics(self) -> None:
        assert chat_ai._is_section_header("\U0001f4ca Market Read")
        assert chat_ai._is_section_header("**\U0001f6e1\ufe0f Security**")
        assert not chat_ai._is_section_header("not a header")
        assert not chat_ai._is_section_header("")

    def test_tool_answer_data_lines_never_headers(self) -> None:
        """Bug fix: a tool answer line starting with a section emoji must not
        become a bold header + separator. Pipe-separated data (the
        ``_format_tool_answer`` price line) and digit-heavy content are data,
        not titles."""
        assert not chat_ai._is_section_header("\U0001f4b0 $0.0001 | 24h: +45%")
        assert not chat_ai._is_section_header("\U0001f4c8 MCap: $50,000,000")
        # short non-numeric titles still ARE headers
        assert chat_ai._is_section_header("\U0001f4ca Market Read")

    def test_tool_answer_price_line_left_plain(self) -> None:
        """Bug fix: the beautifier must NOT rewrite the price_lookup line
        ``💰 $0.0001 | 24h: +45%`` into a bold header + thin separator — it
        stays a plain data line."""
        price_line = "\U0001f4b0 $0.0001 | 24h: +45%"
        assert chat_ai._beautify_answer(price_line) == price_line
        assert "**\U0001f4b0" not in chat_ai._beautify_answer(price_line)
        assert "\u2500" not in chat_ai._beautify_answer(price_line)


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


class TestMarkdownToHtml:
    """Full markdown → HTML conversion (bold + code + italic)."""

    def test_bold_converted(self) -> None:
        assert chat_ai._markdown_to_html("**bold**") == "<b>bold</b>"

    def test_triple_asterisk_converted(self) -> None:
        assert chat_ai._markdown_to_html("***bold***") == "<b>bold</b>"

    def test_inline_code_converted(self) -> None:
        assert chat_ai._markdown_to_html("`0xabc`") == "<code>0xabc</code>"

    def test_inline_code_protects_inner_markdown(self) -> None:
        # Code runs FIRST: **bold** inside backticks must never become <b>.
        assert chat_ai._markdown_to_html("use `**bold**`") == "use <code>**bold**</code>"

    def test_italic_converted(self) -> None:
        assert chat_ai._markdown_to_html("*Live data*") == "<i>Live data</i>"

    def test_underscore_italic_converted(self) -> None:
        assert chat_ai._markdown_to_html("_Live data_") == "<i>Live data</i>"

    def test_intraword_underscores_not_italic(self) -> None:
        # Word-boundary guard: snake_case / code identifiers are NOT emphasis.
        assert chat_ai._markdown_to_html("hello_world_foo") == "hello_world_foo"

    def test_arithmetic_asterisks_not_italic(self) -> None:
        # Multiplication must not be mangled into <i>.
        assert chat_ai._markdown_to_html("x * 2 * y") == "x * 2 * y"

    def test_mixed_markers_convert_together(self) -> None:
        text = "**Bold**, `code`, *italic*"
        assert chat_ai._markdown_to_html(text) == "<b>Bold</b>, <code>code</code>, <i>italic</i>"

    def test_escaped_inner_stays_escaped(self) -> None:
        # Runs AFTER html.escape: &amp; must not double-escape to &amp;amp;.
        assert chat_ai._markdown_to_html("**BTC &amp; ETH**") == "<b>BTC &amp; ETH</b>"
        assert chat_ai._markdown_to_html("`a &lt; b`") == "<code>a &lt; b</code>"
        assert chat_ai._markdown_to_html("*x &amp; y*") == "<i>x &amp; y</i>"

    def test_raw_specials_in_inner_get_escaped(self) -> None:
        # Standalone use on NOT-yet-escaped input must still be safe.
        out = chat_ai._markdown_to_html("**a < b & c**")
        assert out == "<b>a &lt; b &amp; c</b>"
        assert "<script>" not in chat_ai._markdown_to_html("**<script>alert(1)</script>**")

    def test_unmatched_markers_untouched(self) -> None:
        assert chat_ai._markdown_to_html("`unclosed") == "`unclosed"
        assert chat_ai._markdown_to_html("*stray") == "*stray"
        assert chat_ai._markdown_to_html("**open only") == "**open only"

    def test_old_bold_only_alias_unchanged(self) -> None:
        # Backward-compat alias still converts exactly the old cases.
        assert chat_ai._markdown_bold_to_html("**bold**") == "<b>bold</b>"
        assert chat_ai._markdown_bold_to_html("***bold***") == "<b>bold</b>"
        assert chat_ai._markdown_bold_to_html("*stray") == "*stray"

    # ── Block constructs (v13): Telegram has no header/HR/table elements ──

    def test_markdown_header_converted(self) -> None:
        # `## Header` / `### Header` used to leak as literal text — now <b>.
        assert chat_ai._markdown_to_html("## Market Overview") == "<b>Market Overview</b>"
        assert chat_ai._markdown_to_html("### Price Action") == "<b>Price Action</b>"
        # trailing `#`s and inner emphasis are handled too
        assert chat_ai._markdown_to_html("## **Market Overview**") == "<b>Market Overview</b>"
        # 4+ hashes are NOT headers — left literal
        assert chat_ai._markdown_to_html("#### not a header") == "#### not a header"

    def test_separator_converted_to_thin_sep(self) -> None:
        # A lone `---` used to leak as literal dashes — now the thin separator.
        out = chat_ai._markdown_to_html("intro\n---\noutro")
        assert "---" not in out
        assert "\u2500" in out

    def test_pipe_table_converted_to_bullets(self) -> None:
        # A `| table |` that reaches _markdown_to_html directly (no
        # _tables_to_lists in front) still renders as bullets, never a `|` wall.
        src = "| Token | Price | 24h |\n| --- | --- | --- |\n| BONK | $0.00002 | +5% |\n| WIF | $1.20 | -2% |"
        out = chat_ai._markdown_to_html(src)
        assert "|" not in out
        assert "<b>Token · Price · 24h</b>" in out
        assert "• BONK · $0.00002 · +5%" in out
        assert "• WIF · $1.20 · -2%" in out

    def test_ordered_list_converted_to_parenthesis(self) -> None:
        # `1. item` renders as a literal dot in Telegram — now `1) item`.
        assert chat_ai._markdown_to_html("1. first\n2. second\n3. third") == "1) first\n2) second\n3) third"
        # decimals like "1.5x" are NOT list markers
        assert chat_ai._markdown_to_html("1.5x growth continues") == "1.5x growth continues"

    def test_literal_code_tag_converted(self) -> None:
        # The guardrail replaces fabricated addresses inside tags, leaving a
        # literal <code>…</code> the LLM emitted — it must become REAL
        # monospace, never escaped garbage or a raw tag.
        assert chat_ai._markdown_to_html("<code>[address not verified]</code>") == "<code>[address not verified]</code>"
        # entity-escaped variant (a model that double-escaped, or pre-escaped
        # input) converts identically
        assert chat_ai._markdown_to_html("&lt;code&gt;[address not verified]&lt;/code&gt;") == (
            "<code>[address not verified]</code>"
        )
        # balanced tags, no raw HTML re-introduced
        out = chat_ai._markdown_to_html("<code>&lt;script&gt;alert(1)&lt;/script&gt;</code>")
        assert "<script>" not in out
        assert "<code>&lt;script&gt;alert(1)&lt;/script&gt;</code>" in out

    def test_tool_answer_price_line_stays_plain(self) -> None:
        """Bug fix: ``💰 $0.0001 | 24h: +45%`` (the _format_tool_answer price
        line) must render as a plain line — never a bold header + separator.
        The beautifier already stopped treating it as a header; the HTML pass
        must leave it untouched too."""
        price_line = "\U0001f4b0 $0.0001 | 24h: +45%"
        assert chat_ai._markdown_to_html(price_line) == price_line
        assert "<b>" not in chat_ai._markdown_to_html(price_line)


class TestRunLiveToolsBounded:
    """Bug fix: ``_run_live_tools_bounded`` must never swallow
    ``CancelledError``. A cancelled answer pipeline must unwind — it must NOT
    keep executing with an empty ``{}`` result. ``reuse_done`` still returns
    an already-finished task's result instantly."""

    @staticmethod
    async def _slow() -> dict:
        await asyncio.sleep(10)
        return {"price_lookup": "late"}

    def test_cancellation_propagates(self) -> None:
        async def _scenario() -> None:
            task = asyncio.create_task(self._slow())
            waiter = asyncio.create_task(chat_ai._run_live_tools_bounded(task, 5))
            await asyncio.sleep(0.05)  # let the bounded wait begin
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            # The shielded tool task itself must keep running (never
            # cancelled by the propagation) so the guardrail can re-join it.
            assert not task.cancelled()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        _run(_scenario())

    def test_reuse_done_returns_finished_result_instantly(self) -> None:
        async def _scenario() -> None:
            async def _fast() -> dict:
                return {"price_lookup": "data"}

            task = asyncio.create_task(_fast())
            await asyncio.sleep(0.01)  # let the task finish
            result = await chat_ai._run_live_tools_bounded(task, 5, reuse_done=True)
            assert result == {"price_lookup": "data"}

        _run(_scenario())

    def test_timeout_returns_empty_without_reuse(self) -> None:
        async def _scenario() -> None:
            task = asyncio.create_task(self._slow())
            result = await chat_ai._run_live_tools_bounded(task, 0.01)
            assert result == {}
            assert not task.cancelled()  # shielded: task still running
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        _run(_scenario())


class TestCapAnswer:
    """Regression: a 1000-word rambling answer must be trimmed to a sane
    length at a natural boundary before the Telegram chunker."""

    def test_short_answer_unchanged(self) -> None:
        assert chat_ai._cap_answer("short answer") == "short answer"

    def test_long_answer_capped_with_ellipsis(self) -> None:
        long_answer = "Word " * 5000
        out = chat_ai._cap_answer(long_answer)
        assert len(out) <= chat_ai.MAX_ANSWER_CHARS
        assert out.endswith("\u2026")

    def test_cap_prefers_sentence_boundary(self) -> None:
        long_answer = "Sentence with padding here. " * 400
        out = chat_ai._cap_answer(long_answer)
        # trimmed at a sentence end + honest ellipsis — never mid-word
        assert len(out) <= chat_ai.MAX_ANSWER_CHARS
        assert out.endswith("\u2026")
        assert "here." in out

    def test_cap_never_ends_mid_tag(self) -> None:
        long_answer = "text " * 900 + "<br>" + " more text" * 400
        out = chat_ai._cap_answer(long_answer)
        assert len(out) <= chat_ai.MAX_ANSWER_CHARS
        assert not out.endswith("<br>")


class TestStripThinkBlocks:
    """Regression: reasoning-capable models emit <think>...</think> reasoning
    chains; they must never reach the user."""

    def test_complete_block_removed(self) -> None:
        assert chat_ai.strip_think_blocks("Answer.\n<think>hidden chain</think>\nMore.") == "Answer.\n\nMore."

    def test_unterminated_block_removed(self) -> None:
        # max_tokens can cut the model off mid-think: everything from the
        # unclosed <think> onward is reasoning and must be dropped.
        assert chat_ai.strip_think_blocks("Answer.\n<think>cut off by max_tokens") == "Answer."

    def test_whole_answer_is_think_block(self) -> None:
        assert chat_ai.strip_think_blocks("<think>ioneerioneer</think>") == ""

    def test_no_think_block_unchanged(self) -> None:
        text = "Plain answer with <b>bold</b> markup."
        assert chat_ai.strip_think_blocks(text) == text

    def test_multiple_blocks_removed(self) -> None:
        out = chat_ai.strip_think_blocks("<think>a</think>One<think>b</think>Two<think>c</think>")
        assert out == "OneTwo"

    def test_idempotent(self) -> None:
        src = "<think>a</think>visible"
        assert chat_ai.strip_think_blocks(chat_ai.strip_think_blocks(src)) == chat_ai.strip_think_blocks(src)


class TestDegenerateAnswer:
    """Regression: local models can degenerate into bare "-", punctuation-only
    output, or a "ioneerioneerioneer..." token-repetition loop. All must be
    detected so the pipeline replaces them instead of surfacing garbage."""

    def test_bare_dash(self) -> None:
        assert chat_ai.is_degenerate_answer("-") is True
        assert chat_ai.is_degenerate_answer("- ") is True
        assert chat_ai.is_degenerate_answer("*") is True

    def test_empty_and_whitespace(self) -> None:
        assert chat_ai.is_degenerate_answer("") is True
        assert chat_ai.is_degenerate_answer("   ") is True

    def test_punctuation_only(self) -> None:
        assert chat_ai.is_degenerate_answer("...") is True
        assert chat_ai.is_degenerate_answer("!!??") is True
        assert chat_ai.is_degenerate_answer("\u2014 \u2014 \u2014") is True

    def test_emoji_only(self) -> None:
        assert chat_ai.is_degenerate_answer("\U0001f525\U0001f525\U0001f525") is True

    def test_repetition_loop(self) -> None:
        # The exact degeneration seen live: "ioneerioneer..." — carries
        # alphanumerics so a punctuation-only check (backend-side) misses it.
        assert chat_ai.is_degenerate_answer("ioneerioneerioneerioneerioneer") is True
        assert chat_ai.is_degenerate_answer("ioneerioneerioneer") is True

    def test_repeated_word_loop(self) -> None:
        assert chat_ai.is_degenerate_answer("asdfasdfasdfasdf") is True

    def test_real_answer_not_degenerate(self) -> None:
        assert chat_ai.is_degenerate_answer("$BONK is up 12% on strong volume.") is False
        assert chat_ai.is_degenerate_answer("Yes.") is False
        assert chat_ai.is_degenerate_answer("42") is False
        assert chat_ai.is_degenerate_answer("I couldn't find enough context to answer that.") is False


class TestTablesToLists:
    """Regression: Telegram HTML has no table element — a raw markdown pipe
    table renders as a wall of literal pipes. Convert to compact bullets."""

    def test_standard_table_to_bullets(self) -> None:
        src = "| Token | Price | 24h |\n| --- | --- | --- |\n| BONK | $0.00002 | +5% |\n| WIF | $1.20 | -2% |"
        out = chat_ai._tables_to_lists(src)
        assert "|" not in out
        assert "**Token · Price · 24h**" in out  # header bolded
        assert "• BONK · $0.00002 · +5%" in out
        assert "• WIF · $1.20 · -2%" in out
        assert "---" not in out  # separator row dropped

    def test_table_without_separator_converted(self) -> None:
        src = "| A | B |\n| 1 | 2 |\n| 3 | 4 |"
        out = chat_ai._tables_to_lists(src)
        assert "|" not in out
        assert "• A · B" in out

    def test_prose_untouched(self) -> None:
        src = "Plain paragraph with a | pipe inside it."
        assert chat_ai._tables_to_lists(src) == src

    def test_code_fence_untouched(self) -> None:
        src = "```\n| col | col |\n| --- | --- |\n```\n\nReal table:\n| a | b |\n| --- | --- |\n| 1 | 2 |"
        out = chat_ai._tables_to_lists(src)
        # the fenced block is preserved verbatim...
        assert "| col | col |" in out
        assert "| --- | --- |" in out
        # ...the real table is converted
        assert "• 1 · 2" in out

    def test_single_pipe_line_untouched(self) -> None:
        assert chat_ai._tables_to_lists("just | one line") == "just | one line"


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
        """Verify the wire format: persona context_override + explicit history."""
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
        # Tier-1 ships the sharp grounded persona via context_override so the
        # production model gets grounding bullets + anti-hallucination rules.
        ctx = captured["json"]["context_override"]["system_context"]
        assert "PERSONA — Rug Munch Intelligence Analyst" in ctx
        assert "DATA GROUNDING" in ctx  # v12 slim pass renamed from "DATA GROUNDING RULES"
        assert "UNTRUSTED CONTEXT" in ctx  # v14 slim pass merged ANTI-PATTERNS; prompt-injection hardening marker
        assert "ANSWER QUALITY" in ctx


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
        ph.deletes: list[tuple[tuple, dict]] = []

        async def edit_text(*a: object, **k: object) -> None:
            ph.edits.append((a, k))

        async def delete(*a: object, **k: object) -> None:
            ph.deletes.append((a, k))

        ph.edit_text = edit_text
        ph.delete = delete
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

    # Presence checks ("are you there" / "anyone home") must hit the fast
    # warm 1-2 line canned reply — never the slow LLM/RAG path.
    PRESENCE_PHRASES: ClassVar[list[str]] = [
        "are you there",
        "you there",
        "anyone there",
        "anyone home",
        "you alive",
        "u there",
        "there?",
    ]

    @pytest.mark.parametrize("phrase", PRESENCE_PHRASES)
    def test_presence_check_gets_fast_warm_reply(self, phrase: str) -> None:
        reply = chat_ai.local_ai_response(phrase, None)
        assert reply is not None
        assert "I'm here" in reply

    def test_help(self) -> None:
        assert "Rug Munch Capabilities" in (chat_ai.local_ai_response("what can you do", None) or "")

    def test_price_intent(self) -> None:
        assert "Price & Market Data" in (chat_ai.local_ai_response("show me the price of btc", None) or "")

    def test_scan_intent(self) -> None:
        assert "Token Security Scan" in (chat_ai.local_ai_response("is this token safe", None) or "")

    def test_no_match(self) -> None:
        assert chat_ai.local_ai_response("explain yield farming", None) is None


class TestPresenceGateAgreement:
    """Presence checks ("are you there") must pass the tier-2 greeting gate so
    they hit the fast warm canned reply (and agree with it), instead of
    falling through to the slow LLM/RAG path."""

    PRESENCE_PHRASES: ClassVar[list[str]] = TestLocalIntent.PRESENCE_PHRASES

    @pytest.mark.parametrize("phrase", PRESENCE_PHRASES)
    def test_gate_catches_presence_check(self, phrase: str) -> None:
        assert chat_ai._is_greeting_or_thanks(phrase) is True

    @pytest.mark.parametrize("phrase", PRESENCE_PHRASES)
    def test_gate_and_canned_reply_agree(self, phrase: str) -> None:
        assert chat_ai._is_greeting_or_thanks(phrase) is True
        reply = chat_ai.local_ai_response(phrase, None)
        assert reply is not None
        assert "I'm here" in reply

    def test_gate_does_not_match_bare_there(self) -> None:
        # "\bthere\b" alone is far too broad — only the presence phrasings
        # ("anyone there", "there?") count, never "is there a scam here?".
        assert chat_ai._is_greeting_or_thanks("is there a scam here?") is False
        assert chat_ai._is_greeting_or_thanks("what is there to do") is False


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

    def test_null_or_nonnumeric_price_fields_no_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P2 fix: DexScreener null / 'N/A' price fields must NOT crash the
        $TOKEN card — _safe_float fails open to 0.0 instead of raising
        TypeError/ValueError in float() (regression for the P0-adjacent
        float() crash)."""
        _patch_dex(
            monkeypatch,
            pairs=[
                {
                    "baseToken": {"address": "0xabc", "name": "Null Coin"},
                    "chainId": "ethereum",
                    "priceUsd": None,
                    "priceChange": {"h24": None},
                    "volume": {"h24": None},
                    "marketCap": "N/A",
                }
            ],
        )
        msg = _FakeMessage()
        result = _run(chat_ai._lookup_token_symbol("$NULL", msg))
        assert result is True
        assert len(msg.replies) == 1
        text = msg.replies[0][0][0]
        assert "Null Coin" in text

    def test_lookup_sets_shared_sent_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P2 fix: _lookup_token_symbol stamps the shared _TokenCardFlag the
        moment a price card is delivered, so the AI pipeline can detect a card
        landing just after the decide-wait and never double-send."""
        from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing

        flag = _routing._TokenCardFlag()
        assert flag.sent is False
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
        result = _run(chat_ai._lookup_token_symbol("$DOGE", msg, sent_flag=flag))
        assert result is True
        assert flag.sent is True


class TestSendAnswer:
    """Rendering layer (v10 pipeline: strip → tables → beautify → split →
    escape → bold). Verifies no broken tags and the table fallback."""

    def test_table_converted_to_list(self) -> None:
        msg = _FakeMessage()
        answer = "| Token | Risk |\n| --- | --- |\n| BONK | Low |"
        _run(chat_ai._send_answer(msg, answer, 0))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "| Token | Risk |" not in sent
        assert "BONK · Low" in sent

    def test_think_blocks_stripped_at_render(self) -> None:
        msg = _FakeMessage()
        _run(chat_ai._send_answer(msg, "<think>hidden</think>visible", 0))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "hidden" not in sent
        assert "visible" in sent

    def test_bold_and_lists_render_without_broken_tags(self) -> None:
        msg = _FakeMessage()
        answer = "**Bold** text\n- item\n*star item\n*italic* renders"
        _run(chat_ai._send_answer(msg, answer, 0))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "<b>Bold</b>" in sent
        assert sent.count("<b>") == sent.count("</b>")
        assert "<script>" not in sent
        # single-star italic now converts (full _markdown_to_html), while math
        # like "2 * 3 * 4" stays literal — the inner span must start/end on a
        # non-space char.
        assert "<i>italic</i> renders" in sent

    def test_arithmetic_asterisks_stay_literal(self) -> None:
        msg = _FakeMessage()
        _run(chat_ai._send_answer(msg, "2 * 3 * 4 = 24", 0))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "2 * 3 * 4 = 24" in sent
        assert "<i>" not in sent

    def test_long_answer_capped_before_send(self) -> None:
        # A 1000-word wall must never become a multi-part monologue: the
        # answer cap trims it to MAX_ANSWER_CHARS (with an ellipsis) BEFORE
        # the Telegram chunker, so a 12.5k wall becomes at most two bounded
        # parts, never a wall of parts. The exact raw-length bound is asserted
        # directly on _cap_answer (TestCapAnswer); here we verify the send
        # path caps + splits to a bounded number of parts.
        msg = _FakeMessage()
        long_answer = "This is a complete sentence with padding here. " * 200  # ~12.5k chars
        parts_sent = _run(chat_ai._send_answer(msg, long_answer, 0))
        assert parts_sent == 2
        parts = [r[0][0] for r in msg.replies]
        assert len(parts) == 2
        assert all(len(p) < len(long_answer) for p in parts)
        # each part fits the Telegram-safe chunk bound
        assert all(len(p) <= chat_ai.CHUNK_MAX_CHARS for p in parts)
        # honest truncation marker — never a mid-sentence hard cut
        assert parts[-1].endswith("\u2026")

    def test_live_leak_regression_no_raw_markdown_or_html(self) -> None:
        """The live-reported leaks — `##`/`###` headers, `---` separators,
        pipe tables, a literal `<code>[address not verified]</code>` tag and
        numbered lists — must render as real Telegram formatting, never as
        raw markdown/HTML text or a `|` wall."""
        msg = _FakeMessage()
        answer = (
            "## 📊 Market Overview\n"
            "---\n"
            "| Token | 24h |\n"
            "| --- | --- |\n"
            "| BONK | +5% |\n"
            "1. first\n"
            "2. second\n"
            "addr <code>[address not verified]</code>"
        )
        _run(chat_ai._send_answer(msg, answer, 0))
        sent = "".join(r[0][0] for r in msg.replies)
        # headers → real bold, separator → thin line, table → bullets
        assert "<b>📊 Market Overview</b>" in sent
        assert "##" not in sent
        assert "###" not in sent
        assert "---" not in sent
        assert "\u2500" in sent
        assert "BONK · +5%" in sent
        assert "|" not in sent.replace("1)", "").replace("2)", "")
        # ordered list → 1) / 2), never a literal "1." dot
        assert "1) first" in sent
        assert "2) second" in sent
        # literal <code> tag → real monospace, never escaped garbage
        assert "<code>[address not verified]</code>" in sent
        assert "&lt;code&gt;" not in sent
        # balanced tags throughout — Telegram never rejects the part
        assert sent.count("<b>") == sent.count("</b>")
        assert sent.count("<code>") == sent.count("</code>")


class TestRawHtmlNormalization:
    """Regression: local models emit RAW HTML (<b>BTC:</b>, &amp;, &lt;code&gt;)
    instead of Telegram markdown. The renderer must turn it into real, balanced
    Telegram formatting — never literal garbage or unbalanced tags."""

    def test_raw_html_bold_renders_as_bold(self) -> None:
        msg = _FakeMessage()
        _run(chat_ai._send_answer(msg, "<b>BTC:</b> actual volume: $1.2M", 0))
        sent = "".join(r[0][0] for r in msg.replies)
        # Real bold — not the escaped literal &lt;b&gt; that used to render.
        assert "<b>BTC:</b>" in sent
        assert "&lt;b&gt;" not in sent
        assert sent.count("<b>") == sent.count("</b>")

    def test_strong_and_code_tags_normalized(self) -> None:
        msg = _FakeMessage()
        _run(chat_ai._send_answer(msg, "<strong>Risk 82/100</strong> addr <code>0xabc</code>", 0))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "<b>Risk 82/100</b>" in sent
        # <code> normalizes to `code` then converts to REAL monospace — never
        # the literal backtick `` `0xabc` `` that used to render.
        assert "<code>0xabc</code>" in sent
        assert "`0xabc`" not in sent
        assert sent.count("<b>") == sent.count("</b>")
        assert sent.count("<code>") == sent.count("</code>")

    def test_italic_maps_to_real_italic(self) -> None:
        msg = _FakeMessage()
        _run(chat_ai._send_answer(msg, "<i>Live data via tools.</i>", 0))
        sent = "".join(r[0][0] for r in msg.replies)
        # <i> normalizes to _italic_ then converts to REAL <i> — never the
        # literal underscore version that used to render.
        assert "<i>Live data via tools.</i>" in sent
        assert "_Live data via tools._" not in sent
        assert sent.count("<i>") == sent.count("</i>")

    def test_escaped_entities_render_as_literal_chars(self) -> None:
        msg = _FakeMessage()
        _run(chat_ai._send_answer(msg, "TVL &amp; volume for <b>BTC</b>", 0))
        sent = "".join(r[0][0] for r in msg.replies)
        # &amp; renders as & — single-escaped, never the double-escaped garbage.
        assert "&amp;" in sent
        assert "&amp;amp;" not in sent
        assert "<b>BTC</b>" in sent

    def test_mixed_markdown_and_raw_html(self) -> None:
        msg = _FakeMessage()
        answer = "**Header:** <b>BTC:</b> up 5% &amp; volume high"
        _run(chat_ai._send_answer(msg, answer, 0))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "<b>Header:</b>" in sent
        assert "<b>BTC:</b>" in sent
        assert "&amp;" in sent
        assert "&amp;amp;" not in sent
        assert sent.count("<b>") == sent.count("</b>")

    def test_tool_answer_html_renders_not_literal(self) -> None:
        """The no-LLM tool answer (_format_tool_answer emits MARKDOWN) must
        render as formatted Telegram HTML — bold token names, monospace
        addresses, italic footer — never escaped literal markers."""
        msg = _FakeMessage()
        tool_answer = chat_ai._format_tool_answer(
            {
                "price_lookup": ToolResult(
                    tool="price_lookup",
                    ok=True,
                    data={
                        "pairs": [
                            {
                                "name": "Doge Coin",
                                "symbol": "DOGE",
                                "chain": "ethereum",
                                "address": "0xabc",
                                "price_usd": 0.1234,
                                "change_24h": 5.5,
                            }
                        ]
                    },
                )
            }
        )
        assert tool_answer is not None
        _run(chat_ai._send_answer(msg, tool_answer, 0))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "<b>Doge Coin</b>" in sent
        assert "<code>0xabc</code>" in sent
        assert "<i>Live data via Rug Munch tools.</i>" in sent
        assert "&lt;b&gt;" not in sent
        assert "&lt;code&gt;" not in sent
        assert sent.count("<b>") == sent.count("</b>")
        assert sent.count("<code>") == sent.count("</code>")
        assert sent.count("<i>") == sent.count("</i>")

    def test_long_bold_answer_capped_never_unbalanced(self) -> None:
        """A long answer whose **bold** spans contain sentence boundaries must
        never reach Telegram with a dangling ** marker or an unbalanced <b>
        tag — the answer cap trims at a natural boundary and the renderer
        keeps every surviving span balanced (regression for the mid-tag cut)."""
        msg = _FakeMessage()
        answer = "**Risk: 42/100. Check chart.** " * 250  # ~11.5k chars
        _run(chat_ai._send_answer(msg, answer, 0))
        parts = [r[0][0] for r in msg.replies]
        assert len(parts) == 2  # capped to MAX_ANSWER_CHARS, split into bounded parts
        assert all(len(p) < len(answer) for p in parts)
        assert all(p.count("<b>") == p.count("</b>") for p in parts), parts[-1][-80:]
        # Every **bold** span that survived the cap is whole — no dangling
        # marker, no mid-span cut.
        assert all("**" not in p for p in parts), parts[-1][-80:]


class TestGroundingSourcesCount:
    """The "Grounded in N intelligence sources" footer must count the DISTINCT
    sources that actually fed the answer (RAG + live tools + web + market),
    never inflate, and never count failed/empty sources."""

    @staticmethod
    def _tr(*, tool: str = "price_lookup", ok: bool = True, source: str = "dexscreener") -> ToolResult:
        return ToolResult(tool=tool, ok=ok, source=source)

    def test_backend_rag_only(self) -> None:
        assert chat_ai._grounding_sources_count(provider="backend", rag_count=2) == 2

    def test_backend_rag_plus_live_tools(self) -> None:
        live = {"price_lookup": self._tr(), "security_scan": self._tr()}
        assert chat_ai._grounding_sources_count(provider="backend", rag_count=2, live_results=live) == 4

    def test_backend_rag_plus_live_web_tool(self) -> None:
        live = {"web_search": self._tr(tool="web_search", source="searxng")}
        assert chat_ai._grounding_sources_count(provider="backend", rag_count=3, live_results=live) == 4

    def test_knowledge_fallback_web_not_counted(self) -> None:
        # A web_search knowledge fallback is NOT live web data — never count it.
        live = {"web_search": self._tr(tool="web_search", source="knowledge-fallback")}
        assert chat_ai._grounding_sources_count(provider="backend", rag_count=1, live_results=live) == 1

    def test_failed_tools_not_counted(self) -> None:
        live = {"price_lookup": self._tr(ok=False)}
        assert chat_ai._grounding_sources_count(provider="backend", rag_count=1, live_results=live) == 1

    def test_rag_lookup_and_web_excluded_from_tool_bucket(self) -> None:
        live = {
            "web_search": self._tr(tool="web_search", source="searxng"),
            "rag_lookup": self._tr(tool="rag_lookup"),
            "price_lookup": self._tr(),
        }
        # rag bucket(1) + web bucket(1) + price_lookup(1) — no double counting.
        assert chat_ai._grounding_sources_count(provider="backend", rag_count=1, live_results=live) == 3

    def test_fallback_bridge_sources_counted(self) -> None:
        sources = [{"type": "rag"}, {"type": "rag"}, {"type": "web"}]
        assert chat_ai._grounding_sources_count(provider="local", rag_count=3, bridge_sources=sources) == 3

    def test_fallback_with_market_context_and_tools(self) -> None:
        sources = [{"type": "rag"}, {"type": "web"}]
        live = {"price_lookup": self._tr()}
        assert (
            chat_ai._grounding_sources_count(
                provider="local",
                rag_count=0,
                bridge_sources=sources,
                live_results=live,
                has_market_context=True,
            )
            == 4  # rag + web + live tool + market digest
        )

    def test_fallback_rag_result_counts_when_untyped(self) -> None:
        rag = ToolResult(tool="rag_lookup", ok=True, source="intel-db")
        assert chat_ai._grounding_sources_count(provider="local", rag_count=0, rag_result=rag) == 1

    def test_no_grounding_zero(self) -> None:
        assert chat_ai._grounding_sources_count(provider="backend", rag_count=0) == 0
        assert chat_ai._grounding_sources_count(provider="local", rag_count=0) == 0


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
            AsyncMock(return_value={"message": "grounded answer", "rag_count": 2, "tokens": 10, "provider": "backend"}),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "how do honeypots work?", upd.effective_user))
        assert result is True
        assert msg.chat.actions == ["typing"]
        answer_reply = msg.replies[-1][0][0]
        assert "grounded answer" in answer_reply
        # v10 footer: "⚡ Grounded in {N} sources: <names> | Rug Munch Bot" — the
        # backend reports 2 real RAG hits and the generic label names OUR intel.
        assert "Grounded in 2 sources: RMI knowledge base | Rug Munch Bot" in answer_reply
        # Bug fix: the "Thinking..." placeholder is DELETED once the real
        # answer is sent — never edited to a bare ✅ (that reads as a glitch
        # and doubles as a stray message).
        placeholder = msg.returned[0]
        assert len(placeholder.deletes) == 1
        assert placeholder.edits == []

    def test_gibberish_redirect_no_backend_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = AsyncMock(return_value={"message": "should not be called", "rag_count": 0, "provider": "backend"})
        monkeypatch.setattr(chat_ai, "_call_backend_chat", backend)
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "asdf asdf asdf asdf asdf", upd.effective_user))
        assert result is True
        backend.assert_not_called()
        assert "Rug Munch AI" in msg.replies[0][0][0]
        chat_ai.refund_flood.assert_awaited_once()

    def test_backend_down_local_intent_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(chat_ai, "_call_backend_chat", AsyncMock(return_value=None))
        # Degradation test: the whole LLM chain is down, so the tier-2 canned
        # intent reply must take over. Local is explicitly dead here —
        # otherwise a live llama.cpp server would answer and the test would
        # be environment-dependent.
        monkeypatch.setattr(chat_ai, "_call_remote_llm", AsyncMock(return_value=None))
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "what is a honeypot?", upd.effective_user))
        assert result is True
        assert "Token Security Scan" in msg.replies[-1][0][0]

    def test_backend_down_fallback_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(chat_ai, "_call_backend_chat", AsyncMock(return_value=None))
        # Same as above: force the remote fallback to fail so the honest
        # unavailability message is edited into the placeholder.
        monkeypatch.setattr(chat_ai, "_call_remote_llm", AsyncMock(return_value=None))
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "explain yield farming", upd.effective_user))
        assert result is True
        # The "Thinking..." placeholder is created first, then edited to the
        # honest unavailability message when the backend is down.
        assert msg.replies[0][0][0].startswith("\U0001f916")
        assert len(msg.returned) == 1
        placeholder = msg.returned[0]
        # The interim "Still digging" note may have fired first (slow
        # pipeline) — the FINAL placeholder state must be the honest
        # unavailability message.
        assert len(placeholder.edits) >= 1
        assert "AI brain temporarily unavailable" in placeholder.edits[-1][0][0]

    def test_unexpected_pipeline_error_fails_open_with_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail-open boundary: an unexpected exception in the LLM/guardrail
        section must not kill the turn — the user gets the canned fallback
        and the pipeline returns cleanly (single-turn isolation)."""
        from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing

        monkeypatch.setattr(chat_ai, "_call_backend_chat", AsyncMock(return_value=None))
        monkeypatch.setattr(
            _routing,
            "_build_fallback_answer",
            AsyncMock(side_effect=RuntimeError("unexpected crash")),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "explain yield farming", upd.effective_user))
        assert result is True
        placeholder = msg.returned[0]
        assert len(placeholder.edits) >= 1
        assert "AI brain temporarily unavailable" in placeholder.edits[-1][0][0]

    def test_token_card_flag_sent_skips_ai_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P2 race fix: when the $TOKEN price card already landed (shared
        sent flag set), chat_ai_response must NOT also send the AI answer —
        the user gets card + answer otherwise (double-send at the
        decide-wait boundary)."""
        from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing

        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(return_value={"message": "ai answer", "rag_count": 1, "tokens": 10, "provider": "backend"}),
        )
        flag = _routing._TokenCardFlag()
        flag.sent = True  # the card landed while the pipeline was running
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        result = _run(chat_ai.chat_ai_response(upd, None, "$DOGE pump?", upd.effective_user, token_card_flag=flag))
        assert result is True
        # The "Thinking..." placeholder is the ONLY reply — and it is
        # deleted. The AI answer is never surfaced.
        assert not any("ai answer" in r[0][0] for r in msg.replies)
        assert len(msg.returned) == 1
        assert len(msg.returned[0].deletes) == 1


class TestChatAiResponseQuality:
    """End-to-end answer-quality regressions through chat_ai_response:
    think-stripping, degenerate-output replacement, long-answer truncation."""

    @pytest.fixture(autouse=True)
    def _patch_boundaries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(chat_ai, "check_spam", lambda uid: False)
        monkeypatch.setattr(chat_ai, "check_flood", AsyncMock(return_value=(True, 0, None)))
        monkeypatch.setattr(chat_ai, "refund_flood", AsyncMock())

    def test_think_blocks_stripped_before_send(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(
                return_value={
                    "message": "<think>reasoning chain</think>grounded answer",
                    "rag_count": 1,
                    "provider": "backend",
                }
            ),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        _run(chat_ai.chat_ai_response(upd, None, "how do honeypots work?", upd.effective_user))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "grounded answer" in sent
        assert "<think>" not in sent
        assert "reasoning chain" not in sent

    def test_degenerate_repetition_loop_replaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(
                return_value={"message": "ioneerioneerioneerioneerioneer", "rag_count": 0, "provider": "backend"}
            ),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        _run(chat_ai.chat_ai_response(upd, None, "what is going on?", upd.effective_user))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "ioneer" not in sent
        # HTML-escaped in transit (' → &#x27;), so assert on a stable substring.
        assert "put together a proper answer for that" in sent

    def test_bare_dash_replaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(return_value={"message": "-", "rag_count": 0, "provider": "backend"}),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        _run(chat_ai.chat_ai_response(upd, None, "what is going on?", upd.effective_user))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "put together a proper answer for that" in sent

    def test_degenerate_answer_has_no_grounding_footer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A replaced degenerate answer must not claim "Grounded in N sources".
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(return_value={"message": "-", "rag_count": 7, "provider": "backend"}),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        _run(chat_ai.chat_ai_response(upd, None, "what is going on?", upd.effective_user))
        sent = "".join(r[0][0] for r in msg.replies)
        assert "Grounded in" not in sent

    def test_long_backend_answer_capped_not_mid_sentence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Regression for the max_tokens 512 → 1536 fix + the answer cap: the
        # renderer must get the FULL answer but trim it to a sane length at a
        # natural boundary (paragraph/sentence) — never cut mid-sentence, and
        # never turn a simple query into a multi-part 1000-word wall.
        long_answer = "Sentence with padding here. " * 400  # ~25k chars
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(return_value={"message": long_answer, "rag_count": 3, "provider": "backend"}),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        _run(chat_ai.chat_ai_response(upd, None, "explain this long topic?", upd.effective_user))
        parts = [r[0][0] for r in msg.replies if "Sentence with padding" in r[0][0]]
        assert len(parts) == 2  # capped to MAX_ANSWER_CHARS → at most two bounded parts
        # honest truncation marker just before the grounding footer — the
        # cap trimmed the raw answer, never a mid-sentence hard cut
        assert "\u2026\n\n<i>\u26a1 Grounded in 3 sources: RMI knowledge base" in parts[-1]

    def test_footer_counts_live_tools_not_just_rag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bug 2 regression: the "Grounded in N" footer must count the live
        tool sources that actually fed the answer, not only RAG hits."""
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(return_value={"message": "grounded answer", "rag_count": 2, "tokens": 10, "provider": "backend"}),
        )
        monkeypatch.setattr(
            chat_ai,
            "_run_live_tools",
            AsyncMock(
                return_value={
                    "price_lookup": ToolResult(tool="price_lookup", ok=True, source="dexscreener"),
                    "security_scan": ToolResult(tool="security_scan", ok=True, source="rugcheck"),
                }
            ),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        _run(chat_ai.chat_ai_response(upd, None, "what's the price of $BONK?", upd.effective_user))
        sent = "".join(r[0][0] for r in msg.replies)
        # Distinct NAMED sources: the RAG KB counts once + dexscreener +
        # rugcheck = 3 (2 raw RAG hits never inflate the footer).
        assert "Grounded in 3 sources: dexscreener, rugcheck, RMI knowledge base | Rug Munch Bot" in sent

    def test_footer_does_not_count_failed_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(return_value={"message": "grounded answer", "rag_count": 2, "tokens": 10, "provider": "backend"}),
        )
        monkeypatch.setattr(
            chat_ai,
            "_run_live_tools",
            AsyncMock(return_value={"price_lookup": ToolResult(tool="price_lookup", ok=False, error="down")}),
        )
        msg = _FakeMessage()
        upd = _FakeUpdate(12345, "tester", msg)
        _run(chat_ai.chat_ai_response(upd, None, "what's the price of $BONK?", upd.effective_user))
        sent = "".join(r[0][0] for r in msg.replies)
        # Failed tools never inflate the count — stays at the RAG hits.
        assert "Grounded in 2 sources: RMI knowledge base | Rug Munch Bot" in sent


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
            AsyncMock(return_value={"message": "rag answer", "rag_count": 1, "tokens": 10, "provider": "backend"}),
        )
        msg = _FakeMessage(text="how do honeypots work?")
        upd = _FakeUpdate(12345, "tester", msg)
        upd.effective_chat = types.SimpleNamespace(id=999, type="private")
        ctx = types.SimpleNamespace(args=[])
        _run(chat_ai.ai_chat_handler(upd, ctx))
        assert any("rag answer" in r[0][0] for r in msg.replies)

    def test_token_symbol_short_circuits_ai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = AsyncMock(return_value={"message": "nope", "rag_count": 0, "provider": "backend"})
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
        # The $TOKEN lookup now runs CONCURRENTLY with the pipeline (never
        # serializing 10s before it). When the price card lands first it IS
        # the answer — the AI answer is never surfaced even though the
        # pipeline started.
        assert any("DOGE" in r[0][0] for r in msg.replies)
        assert not any("nope" in r[0][0] for r in msg.replies)

    def test_free_text_not_spam_dropped_single_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P0 regression: ai_chat_handler must NOT call check_spam itself —
        the gate lives only in chat_ai_response. The old double call stamped
        _user_last_action, then the second call (ms later) returned True and
        silently dropped EVERY non-owner free-text reply."""
        from app.domains.telegram.rugmunchbot import states as bot_states

        # Use the REAL check_spam (not the fixture's always-False lambda) so
        # the stamp-and-recheck behavior that caused the drop is exercised.
        calls: list[int] = []

        def counting_check_spam(user_id: int) -> bool:
            calls.append(user_id)
            return bot_states.check_spam(user_id)

        monkeypatch.setattr(chat_ai, "check_spam", counting_check_spam)
        monkeypatch.setattr(
            chat_ai,
            "_call_backend_chat",
            AsyncMock(return_value={"message": "rag answer", "rag_count": 1, "tokens": 10, "provider": "backend"}),
        )
        user_id = 987654321  # unique non-owner id, never stamped by other tests
        bot_states._user_last_action.pop(user_id, None)
        msg = _FakeMessage(text="how do honeypots work?")
        upd = _FakeUpdate(user_id, "tester", msg)
        upd.effective_chat = types.SimpleNamespace(id=999, type="private")
        ctx = types.SimpleNamespace(args=[])
        _run(chat_ai.ai_chat_handler(upd, ctx))
        # The spam gate is evaluated exactly ONCE (inside chat_ai_response).
        assert len(calls) == 1
        # And the free-text answer is actually delivered — not dropped.
        assert any("rag answer" in r[0][0] for r in msg.replies)


class TestInterimPlaceholder:
    """Bug fix: the "Still digging" interim note (wired ~5s after the
    placeholder) must keep slow backend queries from looking stalled — and a
    cancelled task must never touch the placeholder."""

    def test_edit_placeholder_if_slow(self) -> None:
        from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing

        async def _scenario() -> None:
            msg = _FakeMessage()
            ph = await msg.reply_text("x")
            await _routing._edit_placeholder_if_slow(ph, delay=0.01)
            assert len(ph.edits) == 1
            assert "Still digging" in ph.edits[0][0][0]

        _run(_scenario())

    def test_cancelled_placeholder_task_never_edits(self) -> None:
        from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing

        async def _scenario() -> None:
            msg = _FakeMessage()
            ph = await msg.reply_text("x")
            task = asyncio.create_task(_routing._edit_placeholder_if_slow(ph, delay=10))
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert ph.edits == []

        _run(_scenario())


class TestTokenCardDecision:
    """Bug fix: the $TOKEN DexScreener lookup runs CONCURRENTLY with the AI
    pipeline — _token_card_won decides at the send point whether the price
    card already won (and never stalls the AI answer)."""

    def test_none_never_wins(self) -> None:
        from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing

        assert _run(_routing._token_card_won(None)) is False

    def test_finished_card_wins(self) -> None:
        from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing

        async def _scenario() -> bool:
            async def _card() -> bool:
                return True

            task = asyncio.create_task(_card())
            await asyncio.sleep(0.01)  # let the lookup finish
            return await _routing._token_card_won(task)

        assert _run(_scenario()) is True

    def test_pending_card_loses_within_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing

        monkeypatch.setattr(_routing, "TOKEN_CARD_DECIDE_WAIT", 0.02)

        async def _scenario() -> bool:
            async def _slow() -> bool:
                await asyncio.sleep(10)
                return True

            task = asyncio.create_task(_slow())
            try:
                return await _routing._token_card_won(task)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        assert _run(_scenario()) is False


class TestNumericVerification:
    """Zero-fabrication NUMBERS layer (chat_ai/_guards.py): a hallucinated
    "$420.69" / "+999%" claim must be flagged inline with "⚠ unverified"
    while a claim matching the tools' real data passes untouched (and a
    no-data answer is left exactly as-is)."""

    @staticmethod
    def _price_result(*, price: float, symbol: str = "BONK", change_24h: float = 2.3) -> ToolResult:
        return ToolResult(
            tool="price_lookup",
            ok=True,
            data={
                "query_symbol": symbol,
                "pairs": [
                    {
                        "symbol": symbol,
                        "price_usd": price,
                        "change_24h": change_24h,
                        "market_cap": 1_234_567_890.0,
                    }
                ],
            },
            symbols=[symbol],
            source="dexscreener",
        )

    def test_hallucinated_price_flagged(self) -> None:
        answer, flagged = _guards._verify_numeric_claims("$BONK is trading at $420.69 today.", [0.00001234])
        assert "$420.69 ⚠ unverified" in answer
        assert flagged == ["$420.69"]

    def test_real_price_passes_unchanged(self) -> None:
        answer = "$BONK is trading at $0.00001234 today."
        out, flagged = _guards._verify_numeric_claims(answer, [0.00001234])
        assert out == answer
        assert flagged == []

    def test_hallucinated_percent_flagged(self) -> None:
        answer, flagged = _guards._verify_numeric_claims("$BONK is up +999% this week.", [2.3, -1.5])
        assert "+999% ⚠ unverified" in answer
        assert flagged == ["+999%"]

    def test_real_percent_passes(self) -> None:
        answer = "$BONK is up +2.3% and down -1.5% yesterday."
        out, flagged = _guards._verify_numeric_claims(answer, [2.3, -1.5])
        assert out == answer
        assert flagged == []

    def test_no_data_returns_answer_unchanged(self) -> None:
        answer = "$420.69 is up +999%."
        out, flagged = _guards._verify_numeric_claims(answer, [])
        assert out == answer
        assert flagged == []

    def test_suffix_amounts_verified(self) -> None:
        # $2.5B claim vs 2.5e9 fetched market cap → verified; $2.6B → flagged.
        answer = "$BONK mcap is $2.5B and FDV is $2.6B."
        out, flagged = _guards._verify_numeric_claims(answer, [2_500_000_000.0])
        assert "$2.5B" in out
        assert "$2.5B ⚠ unverified" not in out
        assert "$2.6B ⚠ unverified" in out
        assert flagged == ["$2.6B"]

    def test_tolerance_absorbs_llm_rounding(self) -> None:
        # ~0.3% and ~0.0006% rounding must NOT be flagged (REL_TOL = 2%).
        answer = "$BONK is at $0.0000123, BTC at $1,234.57."
        out, flagged = _guards._verify_numeric_claims(answer, [0.00001234, 1234.5678])
        assert out == answer
        assert flagged == []

    def test_commas_and_plain_amounts(self) -> None:
        answer = "raised $12,345,678 and the token costs $0.50."
        out, flagged = _guards._verify_numeric_claims(answer, [12345678.0, 0.5])
        assert out == answer
        assert flagged == []

    def test_ticker_dollar_not_treated_as_amount(self) -> None:
        # "$BONK" is a ticker, not a money claim — never flagged.
        answer = "$BONK and $PEPE both moved."
        out, flagged = _guards._verify_numeric_claims(answer, [0.00001234])
        assert out == answer
        assert flagged == []

    def test_unsigned_percent_out_of_scope(self) -> None:
        # Spec scopes the pass to "+X%/-X%"; unsigned prose % is untouched.
        answer = "BONK is up 12% and 42% of supply is locked."
        out, flagged = _guards._verify_numeric_claims(answer, [2.3])
        assert out == answer
        assert flagged == []

    def test_already_flagged_claim_not_double_flagged(self) -> None:
        out, _ = _guards._verify_numeric_claims("$420.69 ⚠ unverified", [0.00001234])
        assert out.count("⚠ unverified") == 1

    def test_collect_numeric_facts_from_tools(self) -> None:
        live = {
            "price_lookup": self._price_result(price=0.00001234),
            "market_overview": ToolResult(
                tool="market_overview",
                ok=True,
                data={
                    "prices": {"bitcoin": 67890.12, "ethereum": 3456.78},
                    "global_market_cap_usd": 2_500_000_000_000.0,
                    "fear_greed": {"value": 72},
                },
                symbols=["BTC", "ETH", "SOL"],
                source="coingecko",
            ),
            "web_search": ToolResult(
                tool="web_search",
                ok=True,
                data={"summary": "BTC went to 67890.12"},  # synthesized — must NOT count
                source="knowledge-fallback",
            ),
            "failed": ToolResult(tool="scanner_intel", ok=False, data={"risk_score": 99}, error="down"),
        }
        facts = _guards._collect_numeric_facts(live)
        assert 0.00001234 in facts
        assert 67890.12 in facts
        assert 3456.78 in facts
        assert 2_500_000_000_000.0 in facts
        assert 72 in facts
        # price pair (3) + market_overview (4); knowledge fallback + failed tool contribute nothing.
        assert len(facts) == 7

    def test_post_guardrail_flags_hallucinated_price(self) -> None:
        """End-to-end through _post_guardrail (tools path): a real fetched
        price + real move pass untouched while a hallucinated price gets the
        inline flag."""

        async def _scenario() -> str:
            async def _done() -> dict[str, ToolResult]:
                return {"price_lookup": self._price_result(price=0.00001234, change_24h=45.0)}

            task = asyncio.create_task(_done())
            await asyncio.sleep(0.01)  # let the task finish
            out, _results, _syms, _addrs, _deg = await _guards._post_guardrail(
                answer="💰 $BONK is at $0.00001234, up +45% — some say $420.69 next.",
                user_id=1,
                provider="tools",
                bridge_sources=[],
                live_tools_task=task,
                rag_result=None,
            )
            return out

        out = _run(_scenario())
        assert "$0.00001234" in out
        assert "$0.00001234 ⚠ unverified" not in out  # real fetched price passes
        assert "+45%" in out
        assert "+45% ⚠ unverified" not in out  # real fetched move passes
        assert "$420.69 ⚠ unverified" in out  # hallucination flagged inline
