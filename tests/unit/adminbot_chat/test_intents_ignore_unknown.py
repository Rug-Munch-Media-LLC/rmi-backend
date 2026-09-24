"""Free-form text is never guessed at a command"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.chat.intents import classify


class T(unittest.TestCase):
    def test_ignores(self) -> None:
        for text in ("what is the weather", "hello", "", "   "):
            assert classify(text) is None, text


if __name__ == "__main__":
    unittest.main()
