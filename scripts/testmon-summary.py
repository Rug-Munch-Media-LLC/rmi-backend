"""Emit testmon statistics lines for the CI step summary."""

import json
import sys

PATH = ".testmon/.testmondata"


def main() -> int:
    """Print the tracked-test counters testmon stored in its data file."""
    with open(PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    print(f"Tests tracked: {len(data.get('tests', {}))}")
    print(f"Files tracked: {len(data.get('files', {}))}")
    print(f"Last updated: {data.get('mtime', 'unknown')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
