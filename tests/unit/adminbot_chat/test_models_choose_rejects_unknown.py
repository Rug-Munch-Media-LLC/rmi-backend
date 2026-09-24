"""An unknown lane is refused rather than stored"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.chat.lanes import LANES
from app.domains.telegram.adminbot.chat.models_choose import choose
from app.domains.telegram.adminbot.chat.models_current import KEY


class FakeContext:
    def __init__(self) -> None:
        self.user_data: dict = {}


class T(unittest.TestCase):
    def test_rejects(self) -> None:
        context = FakeContext()
        assert choose(context, "not-a-lane") == "Unknown model"
        assert KEY not in context.user_data
        known = LANES[0]["id"]
        assert choose(context, known) != "Unknown model"
        assert context.user_data[KEY] == known


if __name__ == "__main__":
    unittest.main()
