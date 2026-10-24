#!/usr/bin/env python3
"""Refuse to publish an artifact that contains anything but the allowlist.

Run against the wheel and/or sdist produced by ``python -m build``. It fails
closed: an unreadable artifact, a forbidden path (scanner, scoring, routers,
databus, app entrypoint) or a missing allowlisted module all exit non-zero.
"""

from __future__ import annotations

import pathlib
import sys
import tarfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "packaging" / "public-modules.txt"

FORBIDDEN = (
    "app/domains/",
    "app/scoring/",
    "app/routers/",
    "app/databus/",
    "app/api/",
    "app/main.py",
    "app/factory.py",
    "app/scanners/",
)


def _allowlist() -> set[str]:
    """Read the published-module allowlist, skipping blanks and comments."""
    listed = []
    for line in MANIFEST.read_text().splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            listed.append(entry)
    return set(listed)


def _names(artifact: pathlib.Path) -> list[str] | None:
    """Entry names inside a wheel or sdist; None when the type is unsupported."""
    if artifact.suffix == ".whl":
        with zipfile.ZipFile(artifact) as zf:
            return zf.namelist()
    if artifact.suffixes[-2:] == [".tar", ".gz"]:
        with tarfile.open(artifact) as tf:
            names = tf.getnames()
        if not names:
            return []
        # sdists are wrapped in a top-level dir; strip it.
        root = names[0].split("/")[0] + "/"
        return [n[len(root) :] for n in names if n.startswith(root)]
    return None


def _forbidden(names: list[str]) -> list[str]:
    """Return the entry names that fall under a forbidden prefix."""
    return [n for n in names if any(n == bad or n.startswith(bad) for bad in FORBIDDEN)]


def _check(artifact: pathlib.Path, failures: list[str]) -> set[str]:
    """Check one artifact; append failures and return the .py entries it carries."""
    names = _names(artifact)
    if names is None:
        failures.append(f"{artifact}: not a wheel or sdist")
        return set()
    failures.extend(f"{artifact.name}: forbidden path {n}" for n in _forbidden(names))
    print(f"{artifact.name}: {len(names)} entries checked")
    return {n for n in names if n.endswith(".py")}


def verify(artifacts: list[pathlib.Path]) -> int:
    """Check every artifact against the allowlist; return a process exit code."""
    expected = _allowlist()
    failures: list[str] = []
    seen: set[str] = set()
    for artifact in artifacts:
        seen |= _check(artifact, failures)
    for rel in sorted(expected):
        if rel not in seen:
            failures.append(f"allowlisted module missing from artifact: {rel}")
    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    print("artifact matches the public allowlist (no scanner/scoring/routers)")
    return 0


if __name__ == "__main__":
    paths = [pathlib.Path(p) for p in sys.argv[1:]]
    if not paths:
        print("usage: verify_public_package.py <wheel|sdist> [...]", file=sys.stderr)
        sys.exit(2)
    sys.exit(verify(paths))
