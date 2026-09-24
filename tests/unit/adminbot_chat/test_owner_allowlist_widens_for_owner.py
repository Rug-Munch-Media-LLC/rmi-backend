"""Write commands are allowed for the owner only"""

from __future__ import annotations

import unittest

import app.domains.telegram.adminbot.chat.owner_commands as unit


class T(unittest.TestCase):
    def test_widens(self) -> None:
        assert unit.allowed_for("systemctl restart nginx", True)
        assert not unit.allowed_for("systemctl restart nginx", False)


if __name__ == "__main__":
    unittest.main()
