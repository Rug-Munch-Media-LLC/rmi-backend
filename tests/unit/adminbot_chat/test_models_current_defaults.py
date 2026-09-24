"""The selected lane defaults to the workhorse"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.chat.models_current import DEFAULT, current


class FakeContext:
    def __init__(self) -> None:
        self.user_data: dict = {}


class T(unittest.TestCase):
    def test_default(self) -> None:
        assert current(FakeContext()) == DEFAULT
        context = FakeContext()
        context.user_data["selected_model"] = "hermes"
        assert current(context) == "hermes"


if __name__ == "__main__":
    unittest.main()
