#!/usr/bin/env python3
"""Export the auto-generated OpenAPI schema from the FastAPI app.

Per T14 (G09 FIX) - never hand-maintain openapi.yaml/openapi.json. Always
auto-generate from the live FastAPI routes. SDK generation (Python, TS)
and MCP manifests depend on this being a faithful snapshot of the code.

Usage:
    python scripts/export_openapi.py              # writes openapi.json to repo root
    python scripts/export_openapi.py --check      # verify path count + exit 1 if < threshold
    python scripts/export_openapi.py --stdout     # print to stdout (for CI piping)
    python scripts/export_openapi.py --out path   # write to a custom path

Why this exists:
    - SDK generators (sdks/python/, sdks/typescript/) consume openapi.json
    - MCP directory manifests need accurate tool paths
    - Schemathesis contract tests run against the live schema
    - Drift between code and docs = drift between reality and what we promise

The export is committed to git so SDK generation works without running the
server. CI re-validates it on every PR.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def get_openapi_schema() -> dict:
    """Build the FastAPI app and return its auto-generated OpenAPI schema."""
    # Ensure repo root is on sys.path so `app.factory` imports work
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from app.factory import create_app

    app = create_app()
    return app.openapi()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "openapi.json"),
        help="Output path for the schema (default: <repo>/openapi.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify path count meets minimum and exit 1 if not",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print schema to stdout instead of writing a file",
    )
    parser.add_argument(
        "--min-paths",
        type=int,
        default=40,
        help="Minimum path count for --check (default: 40)",
    )
    args = parser.parse_args()

    schema = get_openapi_schema()

    path_count = len(schema.get("paths", {}))
    schema_count = len(schema.get("components", {}).get("schemas", {}))

    if args.stdout:
        print(json.dumps(schema, indent=2))

    if args.check:
        if path_count < args.min_paths:
            print(
                f"FAIL: OpenAPI has only {path_count} paths "
                f"(minimum {args.min_paths}). "
                f"Factory imports are likely broken - check that all "
                f"routers in app/api/v1/ and app/domain/* import cleanly.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: OpenAPI has {path_count} paths, {schema_count} schemas")

    if not args.stdout:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(schema, indent=2))
        print(
            f"Wrote {out_path} ({path_count} paths, {schema_count} schemas, "
            f"{out_path.stat().st_size // 1024} KB)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
