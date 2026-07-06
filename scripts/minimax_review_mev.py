#!/usr/bin/env python3
"""MiniMax code review for mev_sandwich_detector.py"""

import contextlib
import json
import os
import subprocess
import urllib.request

code = open("/app/app/mev_sandwich_detector.py").read()[:5000]  # noqa: SIM115

# Get MiniMax API key
key = ""
env_path = "/app/.env"
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            if line.startswith("MINIMAX_API_KEY=") and "***" not in line:
                key = line.strip().split("=", 1)[1]
                break

if not key:
    key = os.environ.get("MINIMAX_API_KEY", "")

if not key:
    with contextlib.suppress(Exception):
        key = subprocess.check_output(["pass", "rmi/minimax/api_key"]).decode().strip()

if not key:
    print("ERROR: No MiniMax API key found")
    exit(1)

body = json.dumps(
    {
        "model": "MiniMax-Text-01",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Review this Python code for bugs, security issues, "
                    "and improvements. List top 3 issues found."
                ),
            },
            {"role": "user", "content": code},
        ],
        "max_tokens": 500,
    }
).encode()

req = urllib.request.Request(
    "https://api.minimax.io/v1/chat/completions",
    data=body,
    headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    },
)
try:
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    print(result["choices"][0]["message"]["content"])
except Exception as e:
    print(f"MiniMax review failed: {e}")
