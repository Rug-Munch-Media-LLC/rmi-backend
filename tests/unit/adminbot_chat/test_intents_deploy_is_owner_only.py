"""Deploy is routed but flagged owner-only"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.chat.intents import OPEN_INTENTS, classify


class T(unittest.TestCase):
    def test_owner_only(self) -> None:
        assert classify("deploy now") == "deploy"
        assert "deploy" not in OPEN_INTENTS


if __name__ == "__main__":
    unittest.main()
