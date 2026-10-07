#!/usr/bin/env python3
"""Generate AI review payload JSON for PR review workflows."""

import json
import os
import tempfile
from pathlib import Path


def main() -> None:
    """Write the AI review payload JSON for the PR review workflow."""
    model = os.environ.get("MODEL", "deepseek/deepseek-chat")
    diff = os.environ.get("DIFF", "")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a senior code reviewer. Review the following diff for: "
                    "1) Security vulnerabilities 2) Logic bugs 3) Performance issues "
                    "4) Missing error handling 5) Code style violations. Be concise. "
                    "Output format: FILE:LINE - ISSUE (severity: HIGH/MEDIUM/LOW) - SUGGESTION"
                ),
            },
            {
                "role": "user",
                "content": "Review this PR diff:\n" + diff,
            },
        ],
        "max_tokens": 2000,
        "temperature": 0.1,
    }

    out_path = Path(os.environ.get("REVIEW_PAYLOAD_PATH") or tempfile.gettempdir()) / "review-payload.json"
    out_path.write_text(json.dumps(payload))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
