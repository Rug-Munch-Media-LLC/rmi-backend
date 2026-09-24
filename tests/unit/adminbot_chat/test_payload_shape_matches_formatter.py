"""Payloads carry the keys the existing alert formatter reads"""

from __future__ import annotations

import unittest

from app.domains.telegram.adminbot.notify.payload import build


class T(unittest.TestCase):
    def test_shape(self) -> None:
        alert = build("HostHealth", "warn", "Host warn: disk 85%", "threshold crossed")
        assert alert["status"] == "firing"
        assert alert["labels"]["alertname"] == "HostHealth"
        assert alert["labels"]["severity"] == "warn"
        assert alert["annotations"]["summary"] == "Host warn: disk 85%"
        assert "startsAt" in alert


if __name__ == "__main__":
    unittest.main()
