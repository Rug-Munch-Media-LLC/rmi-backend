"""Session auth for the X GraphQL lane: cookies -> request headers.

The lane reads timelines as the *logged-in* web client does, with the session
cookies that already exist on this host. Two are needed: `auth_token` (the
session) and `ct0` (the CSRF token, which must also be sent as `x-csrf-token`).

Cookie values are read here and put into headers; they are never logged, never
printed, and never written to the fixture. Missing cookies are reported as an
error code, not raised.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from x_graphql_features import BEARER

COOKIE_JAR = Path(os.environ.get("X_COOKIE_JAR", "/srv/secrets/firefox-cookies.json"))
X_DOMAINS = ("x.com", "twitter.com")
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
NEEDED = ("auth_token", "ct0")


def _is_x(domain: str) -> bool:
    """True only for x.com / twitter.com and their subdomains."""
    host = domain.lower().lstrip(".")
    return any(host == d or host.endswith("." + d) for d in X_DOMAINS)


def jar(path: Path | None = None) -> dict[str, str]:
    """name -> value for the X cookies we need; empty when unreadable."""
    try:
        raw = json.loads((path or COOKIE_JAR).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = raw if isinstance(raw, list) else raw.get("cookies") or []
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("name")) not in NEEDED:
            continue
        if _is_x(str(row.get("domain") or "")):
            out[str(row["name"])] = str(row.get("value") or "")
    return out


def headers(handle: str = "", path: Path | None = None) -> tuple[dict[str, str], str]:
    """(headers, error). error is `no_session_cookies` when auth is unavailable."""
    cookies = jar(path)
    missing = [name for name in NEEDED if not cookies.get(name)]
    if missing:
        return {}, "no_session_cookies:" + ",".join(missing)
    return (
        {
            "authorization": f"Bearer {BEARER}",
            "cookie": f"auth_token={cookies['auth_token']}; ct0={cookies['ct0']}",
            "x-csrf-token": cookies["ct0"],
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "accept": "*/*",
            "content-type": "application/json",
            "referer": f"https://x.com/{handle}" if handle else "https://x.com/",
            "user-agent": UA,
        },
        "",
    )


def require(handle: str = "") -> dict[str, str]:
    """Headers or RuntimeError — for callers that cannot degrade."""
    head, err = headers(handle)
    if err:
        raise RuntimeError(err)
    return head
