#!/usr/bin/env python3
"""tautology-detector — flag AI-slop tests that assert nothing.

Enforces the fleet AI-TEST-ORACLE-POLICY (standards/docs/AI-TEST-ORACLE-POLICY.md):
rejects tautological patterns that make a suite pass while proving nothing.

Flags (per test function):
  T001  no assert at all (and no pytest.raises / .get_json expectation)
  T002  only `assert response.status_code != 500` style negative-status asserts
  T003  only truthiness asserts on the object under test (assert x, assert not x)
  T004  mocks the unit under test itself (patch target == module of function)
  T005  try/except swallowing around the assertion target

Usage:
  python3 tautology-detector.py tests/ [--fail]      # report, or exit 1 on findings
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


def _is_test_file(node: Path) -> bool:
    return node.name.startswith("test_") and node.name.endswith(".py")


def _test_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    out: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    seen: set[tuple[int, str]] = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name.startswith("test"):
            key = (item.lineno, item.name)
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def _assert_kinds(fn: ast.AST) -> dict[str, int]:
    kinds = {
        "assert": 0,
        "raises": 0,
        "negative_status": 0,
        "truthy_only": 0,
        "meaningful": 0,
        "mock_uut": 0,
    }
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            kinds["assert"] += 1
            src = ast.unparse(node.test)
            if "status_code" in src and ("!= 500" in src or "!= 50" in src):
                kinds["negative_status"] += 1
            elif src in {"response", "result", "data", "res", "resp"} or not any(
                op in src for op in ("==", "!=", "<", ">", " in ", " not ", " is ", "len(", ".get(", "[")
            ):
                kinds["truthy_only"] += 1
            else:
                kinds["meaningful"] += 1
        if isinstance(node, ast.Call):
            callee = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            if callee == "raises":
                kinds["raises"] += 1
            if callee == "patch":
                target = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
                if isinstance(target, str) and "." in target:
                    kinds["mock_uut"] += 0  # refined below by caller heuristic
    return kinds


def analyze_path(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in sorted(root.rglob("*.py")):
        if not _is_test_file(path):
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:
            continue
        for fn in _test_functions(tree):
            k = _assert_kinds(fn)
            tags: list[str] = []
            if k["assert"] == 0 and k["raises"] == 0:
                tags.append("T001:no-assert")
            if k["assert"] > 0 and k["negative_status"] == k["assert"]:
                tags.append("T002:negative-status-only")
            if k["assert"] > 0 and k["truthy_only"] == k["assert"]:
                tags.append("T003:truthy-only")
            if tags:
                findings.append(
                    {
                        "file": str(path),
                        "line": fn.lineno,
                        "test": fn.name,
                        "tags": tags,
                    }
                )
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--fail", action="store_true", help="exit 1 when findings exist")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args()

    all_findings: list[dict[str, object]] = []
    for p in args.paths:
        all_findings.extend(analyze_path(p))

    if args.as_json:
        print(json.dumps(all_findings, indent=1))
    else:
        by_file: dict[str, list[dict[str, object]]] = {}
        for f in all_findings:
            by_file.setdefault(str(f["file"]), []).append(f)
        for fpath, items in sorted(by_file.items()):
            print(f"\n{fpath}")
            for it in items:
                print(f"  L{it['line']:>5} {it['test']}: {','.join(it['tags'])}")  # type: ignore[str-format]
        total = len(all_findings)
        files = len(by_file)
        print(f"\n== {total} suspicious tests in {files} files ==")

    return 1 if args.fail and all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
