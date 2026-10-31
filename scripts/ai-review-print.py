"""Print the AI review API response; never fail the job on a parse error."""

import json
import sys

HEADER = "=== AI Code Review ==="
FALLBACK = "\u2139\ufe0f  AI review response parsing failed"


def main(argv: list[str]) -> int:
    """Print the review content, or the non-blocking fallback message."""
    if len(argv) < 2:
        print(FALLBACK)
        return 0
    try:
        with open(argv[1], encoding="utf-8") as handle:
            data = json.load(handle)
        content = data["choices"][0]["message"]["content"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        print(FALLBACK)
        return 0
    print(HEADER)
    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
