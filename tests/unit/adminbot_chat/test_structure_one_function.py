"""Contract test: each governed module declares exactly one function"""

from __future__ import annotations

import ast
import pathlib
import unittest

BASE = pathlib.Path(__file__).resolve().parents[3] / "app" / "domains" / "telegram" / "adminbot"
PACKAGES = ("chat", "notify")
EXEMPT = {"__init__.py"}


def modules() -> list[pathlib.Path]:
    """Every module in the governed packages."""
    return [p for name in PACKAGES for p in sorted((BASE / name).glob("*.py"))]


class T(unittest.TestCase):
    def test_rule(self) -> None:
        """one function per module, so a file cannot grow a second responsibility"""
        offenders: list[str] = []
        for path in modules():
            if path.name in EXEMPT:
                continue
            tree = ast.parse(path.read_text())
            found = [n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
            if len(found) != 1:
                offenders.append(f"{path.name} has {len(found)}")
        assert not offenders, offenders


if __name__ == "__main__":
    unittest.main()
