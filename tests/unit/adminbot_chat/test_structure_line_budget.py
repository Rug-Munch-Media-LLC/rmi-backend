"""Contract test: no governed module exceeds the line budget.

Enforces the house rule mechanically: with this test a violation fails CI
instead of waiting for someone to notice.
"""

from __future__ import annotations

import pathlib
import unittest

BASE = pathlib.Path(__file__).resolve().parents[3] / "app" / "domains" / "telegram" / "adminbot"
BUDGET = 50


class T(unittest.TestCase):
    def test_rule(self) -> None:
        """Every module stays under the budget."""
        offenders: list[str] = []
        for name in ("chat", "notify"):
            for path in sorted((BASE / name).glob("*.py")):
                lines = len(path.read_text().splitlines())
                if lines >= BUDGET:
                    offenders.append(f"{path.name} is {lines}")
        assert not offenders, offenders

    def test_tests_follow_the_same_budget(self) -> None:
        """The contract tests hold themselves to the rule they enforce."""
        here = pathlib.Path(__file__).resolve().parent
        offenders = [
            f"{p.name} is {len(p.read_text().splitlines())}"
            for p in sorted(here.glob("test_*.py"))
            if len(p.read_text().splitlines()) >= BUDGET
        ]
        assert not offenders, offenders


if __name__ == "__main__":
    unittest.main()
