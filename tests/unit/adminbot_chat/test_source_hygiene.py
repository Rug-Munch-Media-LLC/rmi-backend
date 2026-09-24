"""Contract test: shipped source carries no credentials or coupling."""

from __future__ import annotations

import pathlib
import re
import unittest

BASE = pathlib.Path(__file__).resolve().parents[3] / "app" / "domains" / "telegram" / "adminbot"
TOKEN = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}")


class T(unittest.TestCase):
    def test_no_credential_literals(self) -> None:
        """No bot token is embedded in source."""
        for name in ("chat", "notify"):
            for path in (BASE / name).glob("*.py"):
                assert not TOKEN.search(path.read_text()), path.name

    def test_notifier_has_no_backend_coupling(self) -> None:
        """The notifier stays independent of any application service."""
        for path in (BASE / "notify").glob("*.py"):
            text = path.read_text()
            for forbidden in ("rmi_backend", "rugmunchbot"):
                assert forbidden not in text, f"{path.name}: {forbidden}"


if __name__ == "__main__":
    unittest.main()
