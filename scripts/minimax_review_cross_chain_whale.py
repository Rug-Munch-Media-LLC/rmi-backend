#!/usr/bin/env python3
"""MiniMax code review for cross_chain_whale.py"""

import json
import urllib.request

code = open("/app/app/cross_chain_whale.py").read()[:10000]  # noqa: SIM115

k = None
for line in open("/app/.env"):  # noqa: SIM115
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
