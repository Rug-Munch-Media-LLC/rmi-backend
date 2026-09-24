"""Known phrases translate to an existing command"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.chat.intents import classify


class T(unittest.TestCase):
    def test_translates(self) -> None:
        for text, want in (
            ("restart nginx", "svc nginx restart"),
            ("stop the api", "svc api stop"),
            ("show me the docker containers", "dps"),
            ("how are the disks", "disk"),
        ):
            assert classify(text) == want, text


if __name__ == "__main__":
    unittest.main()
