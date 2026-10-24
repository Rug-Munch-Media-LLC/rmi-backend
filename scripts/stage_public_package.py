#!/usr/bin/env python3
"""Stage the allowlisted public package into a build directory.

The backend is an application; only the modules named in
``packaging/public-modules.txt`` are publishable. Staging (rather than packing
the repo in place) is what keeps the private scanner out of the artifact with
no chance of an accidental include: the build directory starts empty.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "packaging" / "public-modules.txt"

PYPROJECT = """[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "rmi-backend-public"
version = "0.1.0"
description = "Public, self-contained RMI backend primitives (allowlisted modules only)"
readme = "README.md"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.115.0", "pydantic>=2.9.0"]

[tool.hatch.build.targets.wheel]
only-include = ["app"]

[tool.hatch.build.targets.sdist]
include = ["app", "pyproject.toml", "README.md"]
"""

README = """# rmi-backend-public

Allowlisted primitives extracted from the Rug Munch Intelligence backend:
typed RFC 7807 error hierarchy (``app.core.errors``, ``app.core.problem_details``).

This package intentionally does NOT contain the scoring engines, scanners,
routers or databus providers. Those are private.
"""


def stage(dest: pathlib.Path) -> int:
    """Copy each allowlisted module into a clean directory, then add metadata."""
    listed = [line.strip() for line in MANIFEST.read_text().splitlines() if line.strip() and not line.startswith("#")]
    if not listed:
        print("manifest is empty", file=sys.stderr)
        return 1
    missing = [rel for rel in listed if not (ROOT / rel).is_file()]
    if missing:
        print(f"allowlisted files missing from the repo: {missing}", file=sys.stderr)
        return 1

    if dest.exists():
        shutil.rmtree(dest)
    for rel in listed:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)

    (dest / "pyproject.toml").write_text(PYPROJECT)
    (dest / "README.md").write_text(README)
    print(f"staged {len(listed)} allowlisted modules into {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(stage(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "dist-public")))
