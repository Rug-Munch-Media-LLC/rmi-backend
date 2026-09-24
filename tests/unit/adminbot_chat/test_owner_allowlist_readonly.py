"""Read-only commands stay open to every allowed user"""

from __future__ import annotations

import unittest

import app.domains.telegram.adminbot.chat.owner_commands as unit


class T(unittest.TestCase):
    def test_readonly(self) -> None:
        for command in ("ls -la", "df -h", "ps aux | head"):
            assert unit.allowed_for(command, False), command


if __name__ == "__main__":
    unittest.main()
