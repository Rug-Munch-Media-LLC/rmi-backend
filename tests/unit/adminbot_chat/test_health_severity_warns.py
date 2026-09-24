"""A host over a warning threshold rates warn"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.notify.health_severity import rate


class T(unittest.TestCase):
    def test_warn(self) -> None:
        level, reason = rate({"load1": 1.0, "disk_pct": 85.0, "mem_pct": 45.0})
        assert level == "warn"
        assert "disk" in reason


if __name__ == "__main__":
    unittest.main()
