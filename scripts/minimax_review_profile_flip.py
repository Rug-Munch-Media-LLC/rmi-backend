#!/usr/bin/env python3
"""MiniMax code review for profile_flip_detector.py"""

import json
import urllib.request

with open("/app/app/profile_flip_detector.py") as f:
    code = f.read()[:10000]

k = ""
with open("/app/.env") as f:
    for line in f:
        line = line.strip()
        if line.startswith("MINIMAX_API_KEY="):
            k = line.split("=", 1)[1].strip()
            break

if not k:
    print("ERROR: MiniMax API key not found")
    exit(1)

body = json.dumps(
    {
        "model": "MiniMax-Text-01",
        "messages": [
            {
                "role": "system",
                "content": "Review this Python code for bugs, security issues, and improvements. Focus ONLY on real bugs, not style. Be concise. List top 3 issues found.",
            },
            {"role": "user", "content": code},
        ],
        "max_tokens": 500,
    }
).encode()

req = urllib.request.Request(
    "https://api.minimax.io/v1/chat/completions",
    data=body,
    headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=30)
data = json.loads(resp.read())
print(data["choices"][0]["message"]["content"])
