"""A calm host rates ok"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.notify.health_severity import rate


class T(unittest.TestCase):
    def test_ok(self) -> None:
        level, _ = rate({"load1": 1.0, "disk_pct": 40.0, "mem_pct": 45.0})
        assert level == "ok"


if __name__ == "__main__":
    unittest.main()
