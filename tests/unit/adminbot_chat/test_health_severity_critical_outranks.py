"""Critical outranks warn so the worst signal is reported"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.notify.health_severity import rate


class T(unittest.TestCase):
    def test_critical(self) -> None:
        level, reason = rate({"load1": 1.0, "disk_pct": 85.0, "mem_pct": 96.0})
        assert level == "critical"
        assert "memory" in reason


if __name__ == "__main__":
    unittest.main()
