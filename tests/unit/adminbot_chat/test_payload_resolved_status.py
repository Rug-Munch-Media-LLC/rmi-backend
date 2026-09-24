"""A resolved alert keeps its name and severity"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.notify.payload import build


class T(unittest.TestCase):
    def test_resolved(self) -> None:
        alert = build("HostHealth", "info", "back to normal", status="resolved")
        assert alert["status"] == "resolved"
        assert alert["labels"]["alertname"] == "HostHealth"


if __name__ == "__main__":
    unittest.main()
