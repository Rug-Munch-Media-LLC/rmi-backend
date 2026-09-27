"""Telegram credential health probe.

Distinguishes a REVOKED credential (a silent wrong state that must fail loudly)
from a transient network fault (which must NOT page).

Contract:
  ok       -> credential accepted by Telegram. Silent.
  revoked  -> Telegram answered 401. Loud. The bot is dead; retrying is pointless.
  unknown  -> network fault, DNS failure, 5xx, timeout, malformed body.
              Retried forever with a bounded backoff and NEVER pages. This is
              the distinction the outage was missing: only a 401 is a page.

Reads the token from the environment, never from argv, so it never appears in
`ps`, a shell history, or a transcript. The token is never logged or printed;
only the bot's numeric id and username are reported.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Final

import httpx

TELEGRAM_API: Final[str] = "https://api.telegram.org"
TIMEOUT_SECONDS: Final[float] = 10.0
BACKOFF_MIN: Final[float] = 60.0
BACKOFF_MAX: Final[float] = 900.0


async def probe_once(token: str) -> tuple[str, str]:
    """Return (state, detail). state is ok | revoked | unknown."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{TELEGRAM_API}/bot{token}/getMe")
    except (httpx.HTTPError, OSError) as exc:
        # DNS, TLS, connect, read timeout, connection reset -> transient.
        return "unknown", type(exc).__name__

    if resp.status_code == 401:
        return "revoked", "HTTP 401 Unauthorized"
    if resp.status_code == 200:
        try:
            username = resp.json().get("result", {}).get("username", "?")
        except ValueError:
            return "unknown", "HTTP 200 unparsable body"
        return "ok", f"HTTP 200 @{username}"
    # 429 rate limit, 5xx server fault, anything else: retry, do not page.
    return "unknown", f"HTTP {resp.status_code}"


async def monitor(name: str, token: str) -> int:
    """Poll getMe until the credential is proven good or proven revoked."""
    backoff = BACKOFF_MIN
    while True:
        state, detail = await probe_once(token)
        if state == "ok":
            print(f"[tg-probe] {name}: OK ({detail})", flush=True)
            backoff = BACKOFF_MIN
            return 0
        if state == "revoked":
            # Loud and terminal. No backoff: a 401 will not fix itself.
            print(
                f"[tg-probe] {name}: CREDENTIAL REVOKED ({detail}). "
                "Telegram delivery from this process is DEAD. "
                "Rotate in the secret store and restart. Refusing to retry.",
                file=sys.stderr,
                flush=True,
            )
            return 2
        print(
            f"[tg-probe] {name}: transient ({detail}); retry in {backoff:.0f}s. Not paging.",
            file=sys.stderr,
            flush=True,
        )
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, BACKOFF_MAX)


async def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "telegram"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print(
            f"[tg-probe] {name}: TELEGRAM_BOT_TOKEN is unset. Cannot verify.",
            file=sys.stderr,
            flush=True,
        )
        return 3
    return await monitor(name, token)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
