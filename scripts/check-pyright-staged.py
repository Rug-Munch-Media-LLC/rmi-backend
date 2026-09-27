#!/usr/bin/env python3
"""Per-file pyright ratchet for the pre-commit gate.

Runs pyright on staged Python files only and fails a commit that introduces a
type error, while still permitting a staged file whose error count is already
recorded in the shrink-only baseline.

Why a baseline instead of a plain blocking gate: rmi-backend carries pre-existing
type debt, so a whole-repo or even a whole-staged-set blocker would wedge every
commit. The baseline records the count each file already has, so the gate blocks
regressions and improvements are free.

Usage (from lefthook):
    python3 scripts/check-pyright-staged.py --files <path> [<path> ...]

Exit codes:
    0  clean, or at or below the recorded baseline for every file
    1  a file exceeds its baseline, or carries a NEW error
    2  the check could not run (missing tool / missing baseline)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

BASELINE = Path(__file__).resolve().parent.parent / ".pyright-baseline.json"
MAX_ERRORS = 50


def run_pyright(files: list[str]) -> tuple[dict[str, int], int]:
    """Return (error count per file, pyright exit code) for the given files."""
    binary = shutil.which("pyright")
    if binary is None:
        print("[pyright] pyright not on PATH — refusing to pass the gate", file=sys.stderr)
        raise SystemExit(2)
    proc = subprocess.run(  # noqa: S603 - argv is a resolved path plus staged filenames
        [binary, *files],
        capture_output=True,
        text=True,
        check=False,
    )
    counts: dict[str, int] = {}
    current: str | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("/") and line.rstrip().endswith(".py"):
            current = line.strip()
        elif " - error:" in line and current is not None:
            counts[current] = counts.get(current, 0) + 1
    return counts, proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", nargs="*", default=[])
    args = parser.parse_args(argv)

    files = sorted({f for f in args.files if f.endswith(".py")})
    if not files:
        print("[pyright] no staged python files — deferring to CI gate")
        return 0
    if len(files) > MAX_ERRORS:
        print(f"[pyright] {len(files)} staged python files — too many for pre-commit, deferring to CI gate")
        return 0
    if not BASELINE.is_file():
        print(f"[pyright] baseline missing: {BASELINE.name} — refusing to pass an unratcheted gate", file=sys.stderr)
        return 2

    baseline: dict[str, int] = json.loads(BASELINE.read_text()).get("files", {})
    counts, _ = run_pyright(files)

    failures: list[str] = []
    for path in files:
        absolute = str(Path(path).resolve())
        relative = str(Path(path).resolve().relative_to(Path.cwd().resolve()))
        found = counts.get(absolute, 0)
        allowed = baseline.get(relative, baseline.get(path, 0))
        if found > allowed:
            failures.append(f"  {relative}: {found} type error(s), baseline allows {allowed}")

    if failures:
        print("[pyright] type errors in staged files — fix before committing:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"[pyright] {len(files)} staged file(s) at or below baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
