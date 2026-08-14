"""Unit tests for the safe multi-part answer helpers (handlers/chat_ai/_parts.py).

Contract under test:
  split_in_parts  — RAW-text splitting into ``<= max_chars`` parts that never
                    breaks a fenced code block or a markdown link / bare URL
  send_in_parts   — async sender: one Telegram message per part, keyboard only
                    on the last part, plain-text degrade on rejection
"""

from __future__ import annotations

import asyncio
import types

from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot.handlers.chat_ai._parts import MAX_PART_CHARS, send_in_parts, split_in_parts


def _run(coro: object) -> object:
    return asyncio.run(coro)


class _FakeChat:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def send_action(self, action: str) -> None:
        self.actions.append(action)


class _FakeMessage:
    def __init__(self) -> None:
        self.chat = _FakeChat()
        self.replies: list[tuple[tuple, dict]] = []
        self.fail_html: bool = False

    async def reply_text(self, *args: object, **kwargs: object) -> object:
        self.replies.append((args, kwargs))
        if self.fail_html and kwargs.get("parse_mode") is not None:
            raise TelegramError("bad entities")
        return types.SimpleNamespace()


class TestSplitInParts:
    def test_short_text_single_part(self) -> None:
        assert split_in_parts("short answer") == ["short answer"]

    def test_empty(self) -> None:
        assert split_in_parts("") == []

    def test_long_prose_split_into_bounded_parts(self) -> None:
        text = "\n\n".join(f"paragraph {i} " + "x" * 3000 for i in range(3))
        parts = split_in_parts(text, max_chars=2000)
        assert len(parts) > 1
        assert all(len(p) <= 2000 for p in parts)

        # Content is lossless (whitespace normalized by the paragraph join).
        def letters(s: str) -> list[str]:
            return [ch for ch in s if ch.isalpha()]

        assert letters("".join(parts)) == letters(text)

    def test_code_block_stays_atomic(self) -> None:
        block = "```python\nprint('hi')\n```"
        text = f"Here is the code:\n\n{block}\n\nDone."
        parts = split_in_parts(text, max_chars=2000)
        assert len(parts) == 1
        assert block in parts[0]

    def test_code_block_never_split_when_it_fits(self) -> None:
        # A code block plus surrounding prose must keep the fence intact even
        # when the total exceeds max_chars (the block itself fits).
        block = "```\n" + "\n".join(f"line{i}" for i in range(12)) + "\n```"
        text = f"Intro paragraph that fills some space.\n\n{block}\n\nOutro."
        parts = split_in_parts(text, max_chars=120)
        assert len(parts) > 1
        # The block appears whole inside exactly one part.
        blocks = [p for p in parts if "```" in p]
        assert len(blocks) == 1
        assert block in blocks[0]

    def test_oversized_code_block_balanced_fences(self) -> None:
        # A single code block larger than max_chars is hard-split with
        # balanced fences in every part.
        block = "```py\n" + "\n".join("print(" + str(i) + ")" for i in range(300)) + "\n```"
        parts = split_in_parts(block, max_chars=200)
        assert len(parts) > 1
        assert all(len(p) <= 200 for p in parts)
        for p in parts:
            fence_count = sum(1 for line in p.split("\n") if line.lstrip().startswith("```"))
            assert fence_count % 2 == 0, f"part has unbalanced fences: {p[:80]!r}"

    def test_markdown_link_not_split(self) -> None:
        url = "https://example.com/very/long/path?q=1"
        link = f"[Rug Munch]({url})"
        text = "Check the docs at " + link + " for details. " + ("more words " * 80)
        parts = split_in_parts(text, max_chars=150)
        assert len(parts) > 1
        assert all(len(p) <= 150 for p in parts)
        for p in parts:
            assert link not in p or p.count("https://example.com/very/long/path") == 1
        # The link must appear whole in exactly one part.
        linked = [p for p in parts if "https://example.com" in p]
        assert len(linked) == 1
        assert link in linked[0]

    def test_bare_url_not_split(self) -> None:
        url = "https://defillama.com/protocol/some-very-long-name/123456789"
        text = f"See {url} for TVL. " + ("filler words " * 60)
        parts = split_in_parts(text, max_chars=120)
        assert len(parts) > 1
        holders = [p for p in parts if "https://defillama.com" in p]
        assert len(holders) == 1
        assert url in holders[0]

    def test_bold_span_never_split(self) -> None:
        """A **bold** span containing sentence boundaries (e.g.
        "**Risk: 42/100. Check chart.**") must never be cut mid-span — no
        dangling ** at a part edge."""
        text = ("intro sentence. " * 10) + "**" + ("y. " * 30) + "**" + ("tail sentence. " * 5)
        parts = split_in_parts(text, max_chars=200)
        assert len(parts) > 1
        for p in parts:
            assert p.count("**") % 2 == 0, p[-80:]
        # The whole span lives in exactly one part.
        spans = [p for p in parts if "**" in p]
        assert len(spans) == 1
        assert spans[0].startswith("**y. ")

    def test_sample_multi_part_split(self) -> None:
        """Sample: a long answer with a code block + links splits into
        Telegram-sized parts with every fence and link intact."""
        answer = (
            "Here is a full answer about honeypots.\n\n"
            "A honeypot contract looks like this:\n\n"
            "```solidity\n"
            "function transfer(address to, uint256 amount) public returns (bool) {\n"
            "    require(!isHoneypot[msg.sender], 'blocked');\n"
            "}\n"
            "```\n\n"
            "For more see https://example.com/honeypots and the "
            "[audit guide](https://example.com/audit).\n\n"
            + "\n\n".join(f"Section {i} covers more detail about the trap and its mechanics." for i in range(80))
        )
        parts = split_in_parts(answer, max_chars=MAX_PART_CHARS)
        assert len(parts) > 1
        assert all(len(p) <= MAX_PART_CHARS for p in parts)
        assert any("```solidity" in p and "blocked" in p for p in parts)  # fence intact
        assert any("https://example.com/honeypots" in p for p in parts)  # URL intact

        # Content is lossless (parts rejoin modulo paragraph separators).
        def letters(s: str) -> list[str]:
            return [ch for ch in s if ch.isalpha()]

        assert letters("".join(parts)) == letters(answer)


class TestSendInParts:
    def test_sends_every_part_keyboard_only_on_last(self) -> None:
        msg = _FakeMessage()
        kb = object()
        _run(
            send_in_parts(
                msg,
                ["part one", "part two", "part three"],
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        )
        assert len(msg.replies) == 3
        assert msg.replies[0][0][0] == "part one"
        assert msg.replies[0][1]["reply_markup"] is None
        assert msg.replies[1][1]["reply_markup"] is None
        assert msg.replies[2][1]["reply_markup"] is kb
        assert all(r[1]["parse_mode"] == "HTML" for r in msg.replies)

    def test_single_part_gets_keyboard(self) -> None:
        msg = _FakeMessage()
        kb = object()
        _run(send_in_parts(msg, ["only"], reply_markup=kb))
        assert len(msg.replies) == 1
        assert msg.replies[0][1]["reply_markup"] is kb

    def test_str_input_split_automatically(self) -> None:
        msg = _FakeMessage()
        long_text = "word " * 1200  # > 4000 chars
        _run(send_in_parts(msg, long_text, parse_mode="HTML"))
        assert len(msg.replies) > 1
        assert all(len(r[0][0]) <= MAX_PART_CHARS for r in msg.replies)

    def test_plain_text_degrade_on_html_rejection(self) -> None:
        msg = _FakeMessage()
        msg.fail_html = True
        _run(send_in_parts(msg, ["<b>bold</b> text"], parse_mode="HTML"))
        assert len(msg.replies) == 2  # HTML attempt + plain-text retry
        assert msg.replies[0][1]["parse_mode"] == "HTML"
        assert msg.replies[1][1].get("parse_mode") is None  # plain-text retry

    def test_empty_parts_no_send(self) -> None:
        msg = _FakeMessage()
        _run(send_in_parts(msg, []))
        assert msg.replies == []
