"""A privileged verb cannot hide behind a harmless command"""

from __future__ import annotations

import unittest

import app.domains.telegram.adminbot.chat.owner_commands as unit


class T(unittest.TestCase):
    def test_blocks(self) -> None:
        for command in ("ls; reboot", "ls && rm -rf /", "ls | shutdown"):
            assert not unit.allowed_for(command, False), command


if __name__ == "__main__":
    unittest.main()
