"""Owner-only verbs win even when they are also read-only allowed"""

from __future__ import annotations

import unittest

import app.domains.telegram.adminbot.chat.owner_commands as unit


class T(unittest.TestCase):
    def test_precedence(self) -> None:
        # systemctl/docker/git appear in BOTH lists. Checking the read-only list
        # first would let any allowed user restart services or push code.
        for command in ("systemctl restart nginx", "docker ps", "git push"):
            assert not unit.allowed_for(command, False), command
            assert unit.allowed_for(command, True), command


if __name__ == "__main__":
    unittest.main()
